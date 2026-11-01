# Specification 02: Deterministic Local Configuration Restoration Engine & Dynamic Secret Exposure Controls

> **Target Release**: `v1.6.3`  
> **Subsystem**: Core Backend & Settings Presentation Layer (`agy-ui/server.py`, `agy-ui/index.html`)  
> **Component**: Offline Local Restoration Engine & Dynamic Warning Visibility  
> **Status**: **READY FOR IMPLEMENTATION**

---

## 1. Problem Statement & Motivation

In the initial design of `v1.6.3` (Spec 01), configuration bundle restoration was delegated to the Antigravity AI Agent via a new chat session using `{ type: "user_prompt" }`. Subsequent security and architectural review revealed two critical flaws in this model:

### 1.1 Credential Exfiltration & LLM Privacy Risk
- Backup bundles contain **raw unencrypted private SSH keys** (`-----BEGIN OPENSSH PRIVATE KEY-----`) and **high-privilege third-party API tokens** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN`).
- Submitting the bundle as a chat prompt transmits all private keys and API tokens over the network to external cloud LLM providers (Google Gemini, Anthropic, etc.).
- This directly violates core security directives prohibiting raw secret exfiltration and risks compliance breaches in enterprise developer environments.

### 1.2 The "Fresh Install" Chicken-and-Egg Dilemma
- On a newly provisioned device or clean installation of Antigravity Studio, the user has **no pre-existing API keys or active Google authentication**.
- Requiring an AI agent to execute restoration introduces an insurmountable circular dependency: **the user cannot run an AI agent to restore credentials without already having valid credentials to authenticate the agent**.

### 1.3 Static Security Warning Friction
- The Export UI unconditionally displayed a stern security warning (`The exported Markdown file contains raw unencrypted private keys and API tokens...`) even when the user only selected non-secret categories such as Custom Skills, MCP Server definitions, or Container Packages.
- The warning should dynamically adapt to user checkbox selections to avoid alert fatigue.

---

## 2. Core Architecture: The Local Deterministic Restoration Engine

Instead of executing restoration through an external LLM agent, Antigravity Studio performs **100% offline, local, deterministic restoration** in the native Python backend (`server.py`).

```mermaid
flowchart TD
    subgraph UI ["Settings Hub (agy-ui/index.html)"]
        U1["User Uploads .agy.md"] --> U2["Two-Phase Validation (Header + SHA-256)"]
        U2 --> U3["Confirmation Sheet Displays Item Tallies"]
        U3 --> U4["User Taps 'Restore Configurations Locally'"]
        U4 --> U5["Send WS Message: import_configurations"]
        U5 --> U6["Receive Real-Time Progress Stream (import_configurations_progress)"]
        U6 --> U7["Display Completion Summary Card & Refresh Settings Lists"]
    end

    subgraph Backend ["AgyWebServer (server.py)"]
        B1["Receive import_configurations payload"] --> B2["Re-verify SHA-256 Checksum"]
        B2 --> B3["Phase 1: Restore SSH Keys (~/.ssh) (0600/0644)"]
        B3 --> B4["Phase 2: Merge API Keys (~/.gemini/config/api_keys.json) (0600)"]
        B4 --> B5["Phase 3: Recursively Restore Skills (~/.gemini/config/skills)"]
        B5 --> B6["Phase 4: Merge MCP Servers (~/.gemini/config/mcp_config.json)"]
        B6 --> B7["Phase 5: Differential Package Check (/etc/apk/world)"]
        B7 --> B8{"Any Missing Packages?"}
        B8 -- "Yes" --> B9["Stream Progress: 'Installing packages via apk add...'"]
        B9 --> B10["Run apk add --no-cache inside APK_MUTATION_LOCK"]
        B8 -- "No" --> B11["Mark All Packages Preserved"]
        B10 --> B12["Emit import_configurations_result with Detailed Summary"]
        B11 --> B12
    end

    U5 -.-> B1
    B9 -.-> U6
    B12 -.-> U7
```

---

## 3. Detailed Specification Requirements

### 3.1 Backend Restoration Pipeline (`server.py`)

A new WebSocket handler handles `mtype == "import_configurations"`:

#### A. Input Payload
```json
{
  "type": "import_configurations",
  "content": "<raw_markdown_bundle_text>"
}
```

#### B. Verification & Parsing
1. Verify Line 1 signature: `<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->`.
2. Extract YAML frontmatter and verify `checksum: "sha256:<hash>"`.
3. Normalize line endings (`\r\n` -> `\n`) and verify SHA-256 digest over the entire body following the closing `---` frontmatter delimiter.
4. If validation fails, immediately return:
   ```json
   {
     "type": "import_configurations_result",
     "success": false,
     "error": "Checksum or signature validation failed"
   }
   ```

#### C. Deterministic Execution Phases

##### Phase 1: SSH Keys
- Target Directory: `~/.ssh/` (mode `0700`).
- Parse each `### SSH Key: \`([^`]+)\`` block.
- Extract `#### Public Key` text and `#### Private Key` text from fenced code blocks.
- **Zero-Overwrite**: Check if `~/.ssh/<name>` or `~/.ssh/<name>.pub` already exists on disk.
  - If exists: skip writing, record `name` in `summary.ssh_keys.preserved`.
  - If missing: write private key with mode `0600`, write public key with mode `0644`. Record in `summary.ssh_keys.restored`.
- Run `sync_ssh_config()` to ensure `IdentityFile ~/.ssh/<name>` entries exist in `~/.ssh/config` (mode `0600`).

##### Phase 2: API Keys
- Target File: `~/.gemini/config/api_keys.json` (mode `0600`).
- Parse fenced JSON block under `## Section: API Keys`.
- Load current key store via `load_api_keys_store()`.
- **Zero-Overwrite**: For each key in the imported dictionary:
  - If key already exists in `store["keys"]`: skip writing, record in `summary.api_keys.preserved`.
  - If missing: insert `{ "value": v["value"], "description": v.get("description", ""), "created_at": int(time.time()) }`. Record in `summary.api_keys.restored`.
- Save via `atomic_write_secure()` and trigger `sync_api_keys_manifest()`.

##### Phase 3: Custom Skills (Recursive Multi-File)
- Target Directory: `~/.gemini/config/skills/`.
- Parse each `### Skill: \`([^`]+)\`` block.
- **Zero-Overwrite**: Check if `~/.gemini/config/skills/<name>/` exists.
  - If exists: skip folder, record in `summary.skills.preserved`.
  - If missing: create directory and extract all nested files from `#### File: \`([^`]+)\`` blocks. Record in `summary.skills.restored`.

##### Phase 4: MCP Servers
- Target File: `~/.gemini/config/mcp_config.json` (mode `0600`).
- Parse fenced JSON block under `## Section: MCP Servers`.
- Load current configuration.
- **Zero-Overwrite**: For each server name in `mcpServers`:
  - If already present: skip, record in `summary.mcps.preserved`.
  - If missing: insert into `mcpServers`. Record in `summary.mcps.restored`.
- Save updated JSON with mode `0600`.

##### Phase 5: Container Packages & Differential `apk add`
- Target: Alpine Linux rootfs (`/etc/apk/world`, `/lib/apk/db/installed`).
- Parse fenced JSON list under `## Section: Alpine Linux Packages`.
- Inspect currently installed packages via `get_installed_packages()` (< 3ms).
- Compare target list against installed packages:
  - For installed items: record in `summary.packages.preserved`.
  - For missing items: record in `missing_pkgs`.
- If `missing_pkgs` is not empty:
  - Emit WebSocket progress event:
    ```json
    {
      "type": "import_configurations_progress",
      "step": "packages",
      "message": "Installing missing packages: nodejs, gcc...",
      "missing": ["nodejs", "gcc"]
    }
    ```
  - Acquire `APK_MUTATION_LOCK`.
  - Execute: `run_apk_command("add", "--no-cache", *missing_pkgs)`.
  - If exit code 0: record all in `summary.packages.installed`.
  - If failed: record in `summary.packages.failed` with error output.

#### D. Completion Response Payload
```json
{
  "type": "import_configurations_result",
  "success": true,
  "summary": {
    "ssh_keys": { "restored": 2, "preserved": 1 },
    "api_keys": { "restored": 3, "preserved": 0 },
    "skills": { "restored": 1, "preserved": 1 },
    "mcps": { "restored": 2, "preserved": 0 },
    "packages": { "installed": 2, "preserved": 4, "failed": 0 }
  }
}
```

---

### 3.2 Frontend UI & Progress State Machine (`agy-ui/index.html`)

#### A. Import Confirmation Sheet Enhancements
- Action Button label updated from *"Start Setup Agent (New Session)"* to:
  `Restore Configurations Locally`
- When clicked:
  1. Hide modal action buttons (`btnExecuteImportConfirm`, `btnCancelImportConfirm`).
  2. Show `#importProgressContainer` inside the sheet:
     - Indeterminate spinning progress ring.
     - Live progress message (`Restoring SSH keys & credentials...`, `Installing packages via apk add...`).
  3. Send `{ type: "import_configurations", content: pendingImportBundle.text }`.

#### B. Live Progress & Completion Display
- On `import_configurations_progress`:
  - Update progress message text with live status.
- On `import_configurations_result`:
  - If failed: display error alert with rejection message and re-enable dismiss button.
  - If success:
    - Replace progress spinner with green success check icon.
    - Render a clean compact badge summary list:
      - 🔑 **SSH Keys**: `N restored` (green badge) | `M preserved` (gray badge)
      - 🔒 **API Keys**: `N merged` (green badge) | `M preserved` (gray badge)
      - ⚡ **Skills**: `N restored` (green badge) | `M preserved` (gray badge)
      - 🖥️ **MCP Servers**: `N added` (green badge) | `M preserved` (gray badge)
      - 📦 **Packages**: `N installed` (green badge) | `M preserved` (gray badge) | `E failed` (red badge)
    - Replace action buttons with a single touch button: `Done`.
    - Tapping `Done` closes the confirmation sheet and dispatches WebSocket refresh queries:
      - `{ type: "list_ssh_keys" }`
      - `{ type: "list_skills" }`
      - `{ type: "list_mcp_servers" }`
      - `{ type: "list_installed_packages" }`

---

### 3.3 Dynamic Export Security Warning Visibility

In `#viewExportImport` inside `agy-ui/index.html`:
- Element: `#exportSecurityWarning`
- Logic: Attach change event listeners to `#chkExportSsh` and `#chkExportApiKeys`.
- Function `updateExportSecurityWarningVisibility()`:
  ```javascript
  const hasSecrets = Boolean(
    (chkExportSsh && chkExportSsh.checked) ||
    (chkExportApiKeys && chkExportApiKeys.checked)
  );
  if (exportSecurityWarning) {
    exportSecurityWarning.style.display = hasSecrets ? 'flex' : 'none';
  }
  ```
- Triggered on initial subview render, on individual checkbox toggle, and on "Select All / Deselect All" clicks.

---

## 4. Architectural Invariants Matrix

| Invariant ID | Rule & Constraint | Verification Mechanism |
| :--- | :--- | :--- |
| **INV-RESTORE-01** | **Zero Network Secret Leakage** | All credential decryption, parsing, and writing occur within `server.py`. Zero calls to `agy` or `{ type: "user_prompt" }`. |
| **INV-RESTORE-02** | **Mandatory Zero-Overwrite** | Existing files/keys are never modified or overwritten; collisions are strictly categorized as `preserved`. |
| **INV-RESTORE-03** | **Strict POSIX Permissions** | `0700` for `~/.ssh/`, `0600` for private keys/tokens/configs, `0644` for public keys. |
| **INV-RESTORE-04** | **APK Concurrency Control** | Package installs must be serialized through `APK_MUTATION_LOCK` using `run_apk_command`. |
| **INV-RESTORE-05** | **Live Feedback for Packages** | When package installations take >500ms, WebSocket progress events keep user informed with active spinner. |
| **INV-RESTORE-06** | **Dynamic Secret Warning** | Warning banner collapses completely when neither SSH keys nor API keys are selected. |

---

## 5. End-to-End Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Touch Screen)
    participant UI as agy-ui (Webview)
    participant Srv as AgyWebServer (server.py)
    participant Host as Internal Storage (~/.ssh, ~/.gemini)
    participant Apk as Alpine Package Manager (apk)

    User->>UI: Selects .agy.md backup file
    UI->>UI: Validates format, signature & SHA-256
    UI->>User: Displays Preview Sheet with detected counts
    User->>UI: Taps "Restore Configurations Locally"
    UI->>UI: Shows progress ring & "Restoring configurations..."
    UI->>Srv: sendWs({ type: "import_configurations", content })
    Srv->>Srv: Re-validates SHA-256 checksum
    Srv->>Host: Writes missing SSH keys (0600) & syncs config
    Srv->>Host: Merges API keys into api_keys.json (0600)
    Srv->>Host: Recursively extracts custom skills
    Srv->>Host: Merges MCP server configurations (0600)
    Srv->>Host: Inspects /etc/apk/world for missing packages
    alt Missing packages detected
        Srv-->>UI: sendWs({ type: "import_configurations_progress", step: "packages", message: "Installing packages..." })
        UI->>User: Updates live status label: "Installing 2 packages..."
        Srv->>Apk: run_apk_command("add", "--no-cache", *missing)
        Apk-->>Srv: Installation complete (code 0)
    end
    Srv-->>UI: sendWs({ type: "import_configurations_result", success: true, summary })
    UI->>User: Displays completion summary badge report & "Done" button
    User->>UI: Taps "Done"
    UI->>UI: Closes modal & reloads active settings lists
```
