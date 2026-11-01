# Specification 01: Settings Export/Import & Autonomous AI Agent Provisioning Bundle

> **Target Release**: `v1.6.3`  
> **Subsystem**: Frontend Presentation & Backend System Management (`agy-ui/index.html`, `agy-ui/server.py`)  
> **Component**: Configuration Backup/Restore Subsystem & Self-Provisioning Prompt Pipeline  
> **Status**: **DRAFT SPECIFICATION FOR ARGON REVIEW**

---

## 1. Problem Statement & Motivation

As users develop workflows inside Antigravity Studio, their environment accumulates critical custom configurations across five distinct subsystems:
1. **Developer SSH Keys**: Ed25519 and RSA private/public keys in `~/.ssh/` used for GitHub/GitLab deployment and remote server operations.
2. **API Keys & Credentials**: Provider tokens (e.g., `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `OPENAI_API_KEY`) stored in `~/.gemini/config/api_keys.json`.
3. **Custom Skills**: User-defined agent workflow instructions and domain guides stored in `~/.gemini/config/skills/`.
4. **Model Context Protocol (MCP) Servers**: Stdio and Streamable HTTP MCP tools configured in `~/.gemini/config/mcp_config.json`.
5. **Alpine Linux Packages**: Compilers, interpreters, and system utilities (`nodejs`, `npm`, `gcc`, `rust`, `python3`, `git`, `ripgrep`) installed in the PRoot container subsystem.

### Migration Friction & Reinstall Burden
When upgrading devices, migrating to a new smartphone, or performing a clean installation of Antigravity Studio, users currently face severe configuration friction:
- Manually recreating dozens of API keys, SSH keys, skills, and MCP definitions through mobile touch menus is tedious and error-prone.
- Traditional app backup solutions rely on opaque binary blobs or raw database dumps that easily break across app versions and cannot adapt to differences in system state.

### The Self-Provisioning Agent Solution
Instead of an opaque database dump, Antigravity Studio `v1.6.3` introduces a **Human-Readable, Agent-Executable Markdown Provisioning Bundle**:
- **Granular Export**: Users select exactly which subsystems to export via touch checkboxes.
- **Self-Documenting & Self-Provisioning**: The generated `.md` file contains both structured configuration payloads and explicit, step-by-step execution directives for the Antigravity AI Agent.
- **Zero-Overwrite Invariant**: The bundle instructs the AI agent to inspect the target system and **never overwrite** existing keys, skills, or MCP servers with matching names.
- **1-Tap Import via New Chat Session**: Importing the `.md` file validates its Antigravity Studio signature, creates a fresh conversation session, and prompts the AI agent to autonomously execute the setup, install missing packages, set strict UNIX permissions (`0600`/`0700`), and present a clean summary report on the mobile screen.

---

## 2. System Architecture & Workflows

```mermaid
flowchart TD
    subgraph Export ["Export Flow"]
        E1["User Opens Settings → Export & Import"] --> E2["User Selects Checkboxes<br/>(SSH, API, Skills, MCP, Packages)"]
        E2 --> E3["User Taps 'Export Backup (.md)'"]
        E3 --> E4["Backend Bundles Data & Calculates SHA-256"]
        E4 --> E5["Embeds Agent Instructions & Invariants"]
        E5 --> E6["Browser Triggers .md Download Prompt<br/>& Staged to /Download/"]
    end

    subgraph Import ["Import & Provisioning Flow"]
        I1["User Opens Settings → Export & Import"] --> I2["User Taps 'Select Backup File'"]
        I2 --> I3["File Picker Uploads .md"]
        I3 --> I4{"Validation Engine<br/>1. Extension == .md<br/>2. Magic Header Present?<br/>3. Valid Frontmatter & Checksum?"}
        I4 -- "Validation Failed" --> I5["Display Mobile Error Alert<br/>(Rejection Reason)"]
        I4 -- "Validation Passed" --> I6["Display Preview Modal<br/>(Counts of Keys, Skills, MCPs, Pkgs)"]
        I6 --> I7["User Taps 'Start Setup Agent'"]
        I7 --> I8["Close Settings & Create New Chat Session"]
        I8 --> I9["Dispatch Markdown as User Prompt"]
        I9 --> I10["AI Agent Autonomously Provisions System"]
        I10 --> I11["Checks Existing Items (Zero-Overwrite)"]
        I11 --> I12["Restores Missing Keys (0600) & Skills"]
        I12 --> I13["Installs Missing Packages (apk add --no-cache)"]
        I13 --> I14["Agent Renders Mobile Completion Summary"]
    end
```

---

## 3. End-to-End Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Mobile UI)
    participant UI as agy-ui (Webview)
    participant Srv as AgyWebServer (server.py)
    participant FS as Host Filesystem
    participant Orch as AgyProcessOrchestrator
    participant Agent as Antigravity AI Agent

    Note over User,FS: Phase 1: Configuration Export
    User->>UI: Selects checkboxes & taps "Export Backup"
    UI->>Srv: WebSocket: { type: "export_configurations", categories: [...] }
    Srv->>FS: Read ~/.ssh/*, api_keys.json, skills/, mcp_config.json, /etc/apk/world
    Srv->>Srv: Compute SHA-256 checksum & format Markdown with Agent Directives
    Srv->>FS: Write backup copy to /storage/emulated/0/Download/
    Srv-->>UI: { type: "export_configurations_result", filename: "...", content: "..." }
    UI->>UI: Trigger browser download via Blob / HTML5 download link
    UI-->>User: Toast: "Export saved to Downloads"

    Note over User,Agent: Phase 2: Configuration Import & Validation
    User->>UI: Taps "Select Backup File" & selects .md
    UI->>UI: Read file via FileReader API
    UI->>UI: Validate Magic Signature (<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->)
    UI->>UI: Validate YAML frontmatter & SHA-256 checksum
    alt Validation Failed
        UI-->>User: Show inline error alert with rejection details
    else Validation Succeeded
        UI->>UI: Show Confirmation Sheet with detected item tallies
        User->>UI: Taps "Start Setup Agent"
        UI->>UI: Close Settings Hub
        UI->>UI: Reset session state (state.conversationId = null)
        UI->>UI: Clear chat viewport & render welcome view
        UI->>Srv: WebSocket: { type: "user_message", prompt_text: <full_md_content> }
        Srv->>Orch: Spawn turn with provisioning prompt
        Orch->>Agent: Deliver markdown prompt
        Agent->>FS: Inspect existing SSH, API keys, skills, MCP configs
        Agent->>FS: Create missing SSH keys (0600), API keys (0600), Skills, MCPs
        Agent->>FS: Run apk add --no-cache for missing world packages
        Agent-->>UI: Stream live progress & final Mobile Summary Report
        UI-->>User: Render completion status in chat
    end
```

---

## 4. UI & DOM Specifications (`agy-ui/index.html`)

### 4.1 Settings Root Menu Entry (`#viewSettingsMenu`)
Within the Settings Hub menu group (`.settings-menu-group`), add a dedicated menu navigation tile:

```html
<!-- Export & Import Menu Item -->
<div class="settings-menu-item" data-target="viewExportImport">
  <div class="settings-menu-icon">
    <svg class="fa-icon" viewBox="0 0 576 512" aria-hidden="true" focusable="false">
      <!-- FontAwesome box-archive / arrow-right-arrow-left icon -->
      <path fill="currentColor" d="M384 121.9c0-6.3-2.5-12.4-7-16.9L313.1 41c-4.5-4.5-10.6-7-17-7H64C28.7 34 0 62.7 0 98v316c0 35.3 28.7 64 64 64h384c35.3 0 64-28.7 64-64V250.1c0-6.3-2.5-12.4-7-16.9l-63.9-63.9c-4.5-4.5-10.6-7-17-7.4H384v-40zM320 68l44 44h-44V68zM48 98c0-8.8 7.2-16 16-16h224v64c0 17.7 14.3 32 32 32h64v240c0 8.8-7.2 16-16 16H64c-8.8 0-16-7.2-16-16V98zm480 94.1V414c0 17.7-14.3 32-32 32s-32-14.3-32-32V224h-48c-8.8 0-16-7.2-16-16v-48h32l96 96.1z"/>
    </svg>
  </div>
  <div class="settings-menu-info">
    <div class="settings-menu-title">Export & Import</div>
    <div class="settings-menu-desc">Backup configs or restore via AI setup agent</div>
  </div>
  <svg class="settings-menu-chevron fa-icon" viewBox="0 0 320 512" aria-hidden="true" focusable="false">
    <path fill="currentColor" d="M278.6 233.4c12.5 12.5 12.5 32.8 0 45.3l-160 160c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L210.7 256 73.4 118.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l160 160z"/>
  </svg>
</div>
```

### 4.2 Subview Layout: `#viewExportImport`
The subview provides two clear mobile touch cards: **Export Configurations** and **Import Configurations**:

```html
<!-- View: Export & Import Configurations -->
<div class="settings-subview" id="viewExportImport">
  <div class="subview-header">
    <button class="icon-btn btn-back-settings" data-back="viewSettingsMenu" title="Back">
      <svg class="fa-icon" viewBox="0 0 448 512" aria-hidden="true"><path fill="currentColor" d="M9.4 233.4c-12.5 12.5-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L109.2 288 416 288c17.7 0 32-14.3 32-32s-14.3-32-32-32l-306.7 0L214.6 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-160 160z"/></svg>
    </button>
    <div class="subview-title">Export & Import</div>
    <button class="icon-btn" id="btnCloseExportImport" title="Close Settings">
      <svg class="fa-icon" viewBox="0 0 384 512" aria-hidden="true"><path fill="currentColor" d="M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z"/></svg>
    </button>
  </div>

  <div class="subview-body">
    <!-- Card 1: Export -->
    <div class="settings-card">
      <div class="card-title">Export Configurations</div>
      <div class="card-subtitle">
        Bundle your selected settings into a self-provisioning Markdown file. Download and import on any device to automatically setup your environment.
      </div>

      <div class="export-checkbox-list">
        <label class="touch-checkbox-row">
          <input type="checkbox" id="chkExportSsh" class="form-checkbox" checked>
          <div class="checkbox-text">
            <span class="checkbox-label">SSH Keys</span>
            <span class="checkbox-desc">Private & public keys in ~/.ssh/</span>
          </div>
        </label>

        <label class="touch-checkbox-row">
          <input type="checkbox" id="chkExportApi" class="form-checkbox" checked>
          <div class="checkbox-text">
            <span class="checkbox-label">API Keys</span>
            <span class="checkbox-desc">Service credentials in api_keys.json</span>
          </div>
        </label>

        <label class="touch-checkbox-row">
          <input type="checkbox" id="chkExportSkills" class="form-checkbox" checked>
          <div class="checkbox-text">
            <span class="checkbox-label">Custom Skills</span>
            <span class="checkbox-desc">Global workflow skills in ~/.gemini/config/skills/</span>
          </div>
        </label>

        <label class="touch-checkbox-row">
          <input type="checkbox" id="chkExportMcp" class="form-checkbox" checked>
          <div class="checkbox-text">
            <span class="checkbox-label">MCP Servers</span>
            <span class="checkbox-desc">Tool configurations in mcp_config.json</span>
          </div>
        </label>

        <label class="touch-checkbox-row">
          <input type="checkbox" id="chkExportPackages" class="form-checkbox" checked>
          <div class="checkbox-text">
            <span class="checkbox-label">Container Packages</span>
            <span class="checkbox-desc">User-installed Alpine Linux packages (world)</span>
          </div>
        </label>
      </div>

      <div class="export-actions-row">
        <button class="btn btn-secondary btn-sm" id="btnToggleSelectAll">Deselect All</button>
        <button class="btn btn-primary" id="btnTriggerExport">
          <svg class="fa-icon btn-icon" viewBox="0 0 512 512"><path fill="currentColor" d="M288 32c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 242.7-73.4-73.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l128 128c12.5 12.5 32.8 12.5 45.3 0l128-128c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L288 274.7 288 32zM64 352c-35.3 0-64 28.7-64 64l0 32c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-32c0-35.3-28.7-64-64-64l-64 0c-17.7 0-32 14.3-32 32s14.3 32 32 32l64 0 0 32L64 448l0-32 64 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L64 352z"/></svg>
          Export Backup (.md)
        </button>
      </div>

      <div class="settings-security-warning">
        <svg class="fa-icon warning-icon" viewBox="0 0 512 512"><path fill="currentColor" d="M256 32c14.2 0 27.3 7.5 34.5 19.8l216 368c7.3 12.4 7.3 27.7 .2 40.1S486.3 480 472 480L40 480c-14.3 0-27.4-7.7-34.7-20.1s-7-27.8 .2-40.1l216-368C228.7 39.5 241.8 32 256 32zm0 128c-13.3 0-24 10.7-24 24l0 112c0 13.3 10.7 24 24 24s24-10.7 24-24l0-112c0-13.3-10.7-24-24-24zm32 224a32 32 0 1 0 -64 0 32 32 0 1 0 64 0z"/></svg>
        <span><strong>Security Notice:</strong> The exported Markdown file contains raw unencrypted private keys and API tokens. Store it securely and never share it publicly.</span>
      </div>
    </div>

    <!-- Card 2: Import -->
    <div class="settings-card">
      <div class="card-title">Import Configurations</div>
      <div class="card-subtitle">
        Upload an Antigravity Studio export file. The AI agent will validate the configuration and autonomously restore missing keys, skills, and tools in a new session.
      </div>

      <input type="file" id="inputImportConfig" accept=".md,text/markdown" style="display:none;">

      <button class="btn btn-primary btn-block" id="btnSelectImportFile">
        <svg class="fa-icon btn-icon" viewBox="0 0 512 512"><path fill="currentColor" d="M288 109.3L288 352c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-242.7-73.4 73.4c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3l128-128c12.5-12.5 32.8-12.5 45.3 0l128 128c12.5 12.5 12.5 32.8 0 45.3s-32.8 12.5-45.3 0L288 109.3zM64 352c-35.3 0-64 28.7-64 64l0 32c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-32c0-35.3-28.7-64-64-64l-64 0c-17.7 0-32 14.3-32 32s14.3 32 32 32l64 0 0 32L64 448l0-32 64 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L64 352z"/></svg>
        Select Backup File (.md)
      </button>

      <!-- Inline Validation Error Alert -->
      <div id="importValidationAlert" class="alert-box error" style="display:none;">
        <div class="alert-title">Import Validation Failed</div>
        <div class="alert-message" id="importValidationMessage"></div>
      </div>
    </div>
  </div>
</div>
```

### 4.3 Import Confirmation Sheet (`#importConfirmSheet`)
When a file passes validation, this sheet confirms the detected items before launching the setup agent:

```html
<div class="action-sheet-overlay" id="importConfirmBackdrop"></div>
<div class="action-sheet bottom-sheet" id="importConfirmSheet" aria-hidden="true">
  <div class="sheet-header">
    <div class="sheet-title">Ready to Restore Configuration</div>
    <button class="icon-btn" id="btnCloseImportConfirm">
      <svg class="fa-icon" viewBox="0 0 384 512"><path fill="currentColor" d="M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z"/></svg>
    </button>
  </div>
  <div class="sheet-body">
    <p class="sheet-desc">
      Valid Antigravity Studio bundle detected. The AI setup agent will launch in a new session to configure missing items:
    </p>
    <ul class="detected-items-list" id="detectedItemsList">
      <!-- Dynamically populated: e.g. <li>2 SSH Keys</li> -->
    </ul>
    <div class="sheet-notice">
      Existing keys, skills, and tools with identical names will <strong>never</strong> be overwritten.
    </div>
    <div class="sheet-actions">
      <button class="btn btn-secondary" id="btnCancelImportConfirm">Cancel</button>
      <button class="btn btn-primary" id="btnExecuteImportConfirm">Start Setup Agent</button>
    </div>
  </div>
</div>
```

---

## 5. Dual-Phase Import Validation Engine

To guarantee security, prevent prompt injection vulnerabilities, and avoid browser hangs, the import pipeline uses a strict two-phase validation engine:

```mermaid
flowchart TD
    A["File Picked (.md)"] --> B{"Phase 1: Syntactic Checks"}
    B -- "Extension != .md or size > 10MB or empty" --> ERR1["Reject: 'Invalid File Format or Size'"]
    B -- "Valid MIME/extension & size" --> C{"Phase 2: Semantic Signature"}
    C -- "Missing Magic Header<br/><!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->" --> ERR2["Reject: 'Not an Antigravity Studio export file'"]
    C -- "Malformed Frontmatter / generator != Antigravity Studio" --> ERR3["Reject: 'Corrupted metadata frontmatter'"]
    C -- "Checksum mismatch" --> ERR4["Reject: 'Checksum verification failed (file modified or corrupted)'"]
    C -- "All Signatures Valid" --> PASS["Validation Passed → Open Confirmation Sheet"]
```

### 5.1 Validation Criteria Matrix

| Criterion | Requirement / Rule | Failure Error Message |
| :--- | :--- | :--- |
| **File Extension** | File name must end in `.md` or `.markdown` (case-insensitive). | *"Please select a valid Markdown (.md) backup file."* |
| **MIME / File Size** | Size must be between 100 bytes and 10 MB (`10,485,760` bytes). | *"File size exceeds 10 MB or file is empty."* |
| **Magic Header** | The exact string `<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->` must appear on Line 1. | *"Invalid backup file: Missing Antigravity Studio export signature."* |
| **YAML Frontmatter** | Delimited by `---` immediately after magic header; contains `generator: "Antigravity Studio"`. | *"Corrupted backup header: Invalid generator specification."* |
| **Export Version** | `export_version` must equal `"1.0"`. | *"Unsupported export version. Please update Antigravity Studio."* |
| **Category Declarations** | `categories:` array contains at least one recognized category (`ssh_keys`, `api_keys`, `skills`, `mcps`, `packages`). | *"Export contains no valid configuration sections."* |
| **SHA-256 Checksum** | `checksum: sha256:<hex>` must match the SHA-256 hash of the content following the frontmatter `---`. | *"Integrity check failed: Checksum mismatch indicates file was modified or corrupted."* |

### 5.2 Client-Side Validation Implementation
```javascript
async function validateExportMarkdown(file) {
  // 1. Extension & Size check
  const name = file.name.toLowerCase();
  if (!name.endsWith('.md') && !name.endsWith('.markdown')) {
    throw new Error('Please select a valid Markdown (.md) backup file.');
  }
  if (file.size < 64) {
    throw new Error('Selected file is empty.');
  }
  if (file.size > 10 * 1024 * 1024) {
    throw new Error('File exceeds the 10 MB maximum backup limit.');
  }

  // 2. Read text content
  const text = await file.text();

  // 3. Magic header check (must be line 1)
  const lines = text.split(/\r?\n/);
  if (lines[0].trim() !== '<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->') {
    throw new Error('Invalid backup file: Missing Antigravity Studio export signature on Line 1.');
  }

  // 4. Extract frontmatter
  const fmMatch = text.match(/^<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->\s*\r?\n---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$/);
  if (!fmMatch) {
    throw new Error('Corrupted export file: Missing YAML frontmatter block.');
  }

  const rawFrontmatter = fmMatch[1];
  const payloadBody = fmMatch[2];

  // Parse simple YAML fields
  const meta = {};
  for (const fline of rawFrontmatter.split(/\r?\n/)) {
    const kv = fline.match(/^([a-z_]+):\s*(.+)$/i);
    if (kv) {
      meta[kv[1].trim()] = kv[2].trim().replace(/^["']|["']$/g, '');
    }
  }

  if (meta.generator !== 'Antigravity Studio') {
    throw new Error('Invalid generator: File was not created by Antigravity Studio.');
  }
  if (meta.export_version !== '1.0') {
    throw new Error(`Unsupported export version (${meta.export_version || 'unknown'}).`);
  }

  // 5. Verify Checksum (Normalize CRLF to LF to ensure cross-platform consistency)
  if (meta.checksum && meta.checksum.startsWith('sha256:')) {
    const expectedHash = meta.checksum.split(':')[1].trim();
    const normalizedPayload = payloadBody.replace(/\r\n/g, '\n').trim();
    const encoder = new TextEncoder();
    const data = encoder.encode(normalizedPayload);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const computedHash = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');

    if (computedHash !== expectedHash) {
      throw new Error('Integrity check failed: The export file content does not match its SHA-256 checksum.');
    }
  }

  // 6. Extract summary tallies for confirmation sheet
  const tallies = {
    ssh_keys: (payloadBody.match(/### SSH Key:\s*`([^`]+)`/g) || []).length,
    api_keys: (payloadBody.match(/"value":\s*"[^"]+"/g) || []).length,
    skills: (payloadBody.match(/### Skill:\s*`([^`]+)`/g) || []).length,
    mcps: (payloadBody.match(/### MCP Server:\s*`([^`]+)`/g) || []).length,
    packages: 0,
  };

  const pkgMatch = payloadBody.match(/## Section: Alpine Linux Packages[\s\S]*?```json\s*([\s\S]*?)\s*```/);
  if (pkgMatch) {
    try {
      const pkgs = JSON.parse(pkgMatch[1]);
      tallies.packages = Array.isArray(pkgs) ? pkgs.length : 0;
    } catch (_) {}
  }

  return { text, meta, tallies };
}
```

---

## 6. Generated Markdown File Blueprint & Agent Directives

The generated `.md` file is structured with precision so that any LLM agent receives unambiguous instructions, standard Unix paths, and strict safety rules:

````markdown
<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->
---
generator: "Antigravity Studio"
export_version: "1.0"
exported_at: "2026-11-01T00:30:00Z"
source_app_version: "1.6.3"
categories:
  - ssh_keys
  - api_keys
  - skills
  - mcps
  - packages
checksum: "sha256:3a1b8c4d9e0f...7a"
---

# Antigravity Studio Environment Self-Provisioning Bundle

> **NOTICE TO AI AGENT**: You have been invoked to restore an exported configuration bundle inside Antigravity Studio on Android. Follow the instructions and safety invariants below strictly.

---

## Agent Execution Directives & Safety Invariants

You are operating inside the Antigravity Studio environment backed by an integrated Alpine Linux container subsystem (PRoot). Standard developer paths are:
- Internal Home: `~` (maps to `/data/data/com.termux/files/home` or `/data/data/ai.agy.app/files/home`)
- SSH Directory: `~/.ssh/`
- Configuration Directory: `~/.gemini/config/`
- API Keys Store: `~/.gemini/config/api_keys.json`
- API Keys Manifest: `~/.gemini/config/rules/api_keys_manifest.md`
- Skills Directory: `~/.gemini/config/skills/`
- MCP Config: `~/.gemini/config/mcp_config.json`

### Mandatory Safety Rules:
1. **CRITICAL ZERO-OVERWRITE INVARIANT**:
   - **NEVER** overwrite an existing key, file, skill, or MCP server if one with the same name already exists on disk.
   - For every item in this document:
     - **SSH Keys**: Check if `~/.ssh/<name>` or `~/.ssh/<name>.pub` exists. If it exists, skip it and record as `[PRESERVED]`.
     - **API Keys**: Read `~/.gemini/config/api_keys.json`. If key name already exists, skip it and record as `[PRESERVED]`.
     - **Skills**: Check if directory `~/.gemini/config/skills/<skill_name>/` exists. If it exists, skip it and record as `[PRESERVED]`.
     - **MCP Servers**: Read `~/.gemini/config/mcp_config.json`. If server name already exists in `mcpServers`, skip it and record as `[PRESERVED]`.
     - **Packages**: Check if package is installed via `apk info -e <pkg_name>`. If installed, skip it and record as `[PRESERVED]`.
2. **STRICT UNIX PERMISSIONS**:
   - `~/.ssh` directory must have permissions `0700` (`chmod 700 ~/.ssh`).
   - Private SSH keys must have permissions `0600` (`chmod 600 ~/.ssh/<name>`).
   - Public SSH keys must have permissions `0644` (`chmod 644 ~/.ssh/<name>.pub`).
   - `api_keys.json` and `mcp_config.json` must strictly have permissions `0600` (`chmod 600 <file>`).
3. **PACKAGE INSTALLATION**:
   - For all missing packages listed under Alpine Linux Packages, install in batch using:
     `apk add --no-cache <pkg1> <pkg2> ...` via your `run_command` tool.
4. **COMPLETION REPORTING**:
   - At the conclusion of execution, output a clear, compact summary formatted for a narrow mobile screen using markdown bullet points:
     - `[CREATED]` items successfully added.
     - `[PRESERVED]` items skipped because they already existed.
     - `[INSTALLED]` container packages added.
     - `[ERRORS]` any failures encountered (or "None").

---

## Section: SSH Keys

### SSH Key: `id_ed25519`
- **Key Type**: ED25519
- **Fingerprint**: `SHA256:xyz...`

#### Public Key (`~/.ssh/id_ed25519.pub`)
```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@antigravity
```

#### Private Key (`~/.ssh/id_ed25519`)
```text
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAA...
-----END OPENSSH PRIVATE KEY-----
```

---

## Section: API Keys

Configure into `~/.gemini/config/api_keys.json` and regenerate `~/.gemini/config/rules/api_keys_manifest.md`:

```json
{
  "OPENAI_API_KEY": {
    "value": "sk-proj-...",
    "description": "OpenAI platform production token"
  },
  "TAVILY_API_KEY": {
    "value": "tvly-...",
    "description": "Tavily search API token"
  }
}
```

---

## Section: Custom Skills

### Skill: `argon-agent`
- **Folder**: `~/.gemini/config/skills/argon-agent/`
- **File**: `~/.gemini/config/skills/argon-agent/SKILL.md`

```markdown
---
name: argon-agent
description: Architectural review, security auditing, and system hardening specialist.
---
# Argon Agent System Instructions...
```

---

## Section: MCP Servers

Merge the following entries into the `mcpServers` object in `~/.gemini/config/mcp_config.json`:

```json
{
  "filesystem": {
    "command": "npx",
    "args": [
      "-y",
      "@modelcontextprotocol/server-filesystem",
      "/storage/emulated/0/Documents"
    ],
    "env": {},
    "disabled": false
  },
  "github": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {
      "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."
    },
    "disabled": false
  }
}
```

---

## Section: Alpine Linux Packages

Verify and install missing packages with `apk add --no-cache`:

```json
[
  "nodejs",
  "npm",
  "gcc",
  "musl-dev",
  "ripgrep"
]
```
````

---

## 7. Backend Implementation Specifications (`agy-ui/server.py`)

### 7.1 WebSocket Protocol Handler
In `server.py`, register the message handler for `export_configurations`:

```python
if mtype == "export_configurations":
    categories = data.get("categories") or ["ssh_keys", "api_keys", "skills", "mcps", "packages"]
    try:
        filename, content, dest = await generate_config_export_bundle(categories)
        await websocket.send(json.dumps({
            "type": "export_configurations_result",
            "success": True,
            "filename": filename,
            "content": content,
            "destination": dest,
        }))
        await self.broadcast({
            "type": "system_card",
            "title": "Configuration Exported",
            "markdown": f"Exported `{filename}` to `{dest}`.",
            "level": "info",
        })
    except Exception as exc:
        logger.exception("Export failed: %s", exc)
        await websocket.send(json.dumps({
            "type": "export_configurations_result",
            "success": False,
            "error": str(exc),
        }))
    return
```

### 7.2 Export Bundle Generator Function
```python
async def generate_config_export_bundle(categories: list[str]) -> tuple[str, str, str]:
    now_ts = time.strftime("%Y%m%d_%H%M%S")
    iso_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    filename = f"AntigravityStudio-Backup-{now_ts}.agy.md"

    body_sections: list[str] = []

    # 1. SSH Keys
    if "ssh_keys" in categories:
        keys = await list_ssh_keys()
        ssh_blocks = []
        for k in keys:
            name = k["name"]
            pub_path = SSH_DIR / f"{name}.pub"
            priv_path = SSH_DIR / name
            pub_text = pub_path.read_text(encoding="utf-8").strip() if pub_path.exists() else ""
            priv_text = priv_path.read_text(encoding="utf-8").strip() if priv_path.exists() else ""
            if pub_text and priv_text:
                ssh_blocks.append(
                    f"### SSH Key: `{name}`\n"
                    f"- **Key Type**: {k.get('key_type', 'UNKNOWN')}\n"
                    f"- **Fingerprint**: `{k.get('fingerprint', 'N/A')}`\n\n"
                    f"#### Public Key (`~/.ssh/{name}.pub`)\n```text\n{pub_text}\n```\n\n"
                    f"#### Private Key (`~/.ssh/{name}`)\n```text\n{priv_text}\n```"
                )
        if ssh_blocks:
            body_sections.append("## Section: SSH Keys\n\n" + "\n\n---\n\n".join(ssh_blocks))

    # 2. API Keys
    if "api_keys" in categories:
        store = load_api_keys_store()
        raw_keys = store.get("keys", {})
        if raw_keys:
            export_keys = {
                k: {"value": v.get("value", ""), "description": v.get("description", "")}
                for k, v in raw_keys.items() if v.get("value")
            }
            body_sections.append(
                "## Section: API Keys\n\n"
                "Configure into `~/.gemini/config/api_keys.json` and regenerate `~/.gemini/config/rules/api_keys_manifest.md`:\n\n"
                f"```json\n{json.dumps(export_keys, indent=2)}\n```"
            )

    # 3. Custom Skills
    if "skills" in categories and GLOBAL_SKILLS_DIR.is_dir():
        skill_blocks = []
        for sdir in sorted(GLOBAL_SKILLS_DIR.iterdir()):
            if sdir.is_dir() and (sdir / "SKILL.md").exists():
                skill_content = (sdir / "SKILL.md").read_text(encoding="utf-8")
                skill_blocks.append(
                    f"### Skill: `{sdir.name}`\n"
                    f"- **Folder**: `~/.gemini/config/skills/{sdir.name}/`\n"
                    f"- **File**: `~/.gemini/config/skills/{sdir.name}/SKILL.md`\n\n"
                    f"```markdown\n{skill_content}\n```"
                )
        if skill_blocks:
            body_sections.append("## Section: Custom Skills\n\n" + "\n\n---\n\n".join(skill_blocks))

    # 4. MCP Servers
    if "mcps" in categories and MCP_CONFIG_FILE.exists():
        try:
            mcp_data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
            servers = mcp_data.get("mcpServers", {})
            if servers:
                body_sections.append(
                    "## Section: MCP Servers\n\n"
                    "Merge the following entries into `mcpServers` in `~/.gemini/config/mcp_config.json`:\n\n"
                    f"```json\n{json.dumps(servers, indent=2)}\n```"
                )
        except Exception as exc:
            logger.warning("Could not parse MCP config for export: %s", exc)

    # 5. Packages
    if "packages" in categories:
        pkg_data = get_installed_packages()
        pkg_names = [p["name"] for p in pkg_data.get("packages", [])]
        if pkg_names:
            body_sections.append(
                "## Section: Alpine Linux Packages\n\n"
                "Verify and install missing packages with `apk add --no-cache`:\n\n"
                f"```json\n{json.dumps(pkg_names, indent=2)}\n```"
            )

    payload_body = "\n\n---\n\n".join(body_sections)

    # Normalize newlines to LF for cross-platform SHA-256 computation
    normalized_payload = payload_body.replace("\r\n", "\n").strip()
    checksum = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()

    # Frontmatter Header
    header = (
        f"<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->\n"
        f"---\n"
        f'generator: "Antigravity Studio"\n'
        f'export_version: "1.0"\n'
        f'exported_at: "{iso_ts}"\n'
        f'source_app_version: "1.6.3"\n'
        f"categories:\n" + "\n".join(f"  - {c}" for c in categories) + "\n"
        f'checksum: "sha256:{checksum}"\n'
        f"---\n\n"
        f"# Antigravity Studio Environment Self-Provisioning Bundle\n\n"
        f"> **NOTICE TO AI AGENT**: You have been invoked to restore an exported configuration bundle inside Antigravity Studio on Android. Follow the instructions and safety invariants below strictly.\n\n"
        f"---\n\n"
        f"## Agent Execution Directives & Safety Invariants\n\n"
        f"You are operating inside the Antigravity Studio environment backed by an integrated Alpine Linux container subsystem (PRoot).\n\n"
        f"### Mandatory Safety Rules:\n"
        f"1. **CRITICAL ZERO-OVERWRITE INVARIANT**: NEVER overwrite an existing key, file, skill, or MCP server if one with the same name already exists on disk.\n"
        f"   - **SSH Keys**: Check `~/.ssh/<name>` and `~/.ssh/<name>.pub`. If present, skip and mark `[PRESERVED]`.\n"
        f"   - **API Keys**: Read `~/.gemini/config/api_keys.json`. If key already exists, skip and mark `[PRESERVED]`.\n"
        f"   - **Skills**: Check `~/.gemini/config/skills/<name>/`. If present, skip and mark `[PRESERVED]`.\n"
        f"   - **MCP Servers**: Read `~/.gemini/config/mcp_config.json`. If present, skip and mark `[PRESERVED]`.\n"
        f"   - **Packages**: Check `apk info -e <pkg>`. If installed, skip and mark `[PRESERVED]`.\n"
        f"2. **STRICT UNIX PERMISSIONS**: Set `~/.ssh` to 0700, private keys to 0600, public keys to 0644, and config JSON files to 0600.\n"
        f"3. **PACKAGE INSTALLATION**: Install missing packages with `apk add --no-cache <pkg1> <pkg2> ...`.\n"
        f"4. **COMPLETION REPORTING**: Render a clean mobile summary list using `[CREATED]`, `[PRESERVED]`, `[INSTALLED]`, and `[ERRORS]` badges.\n\n"
    )

    full_content = header + payload_body

    # Save copy to /storage/emulated/0/Download/
    download_dir = Path("/storage/emulated/0/Download")
    download_dir.mkdir(parents=True, exist_ok=True)
    dest_path = download_dir / filename
    dest_path.write_text(full_content, encoding="utf-8")

    return filename, full_content, str(dest_path)
```

---

## 8. Frontend Session Handlers & Prompt Dispatching

When the user taps **"Start Setup Agent"** in `#importConfirmSheet`:
1. The modal and Settings Hub are dismissed immediately.
2. The active conversation state is reset to a clean new turn:
   ```javascript
   function launchProvisioningSession(markdownPrompt) {
     // Close modals and hub
     closeImportConfirmSheet();
     closeSettingsHub();

     // Reset session state
     if (state.conversationId) {
       const curBuf = getOrCreateSessionBuffer(state.conversationId);
       curBuf.draftPrompt = '';
     }
     state.conversationId = null;
     if (window.AndroidApp && typeof window.AndroidApp.setActiveSession === 'function') {
       window.AndroidApp.setActiveSession(null);
     }
     chatViewport.innerHTML = '';
     state.activeTurns.clear();
     renderEmptyWelcome();
     setInitializingControlsState(false);
     updatePinButtonState(false);
     stopLiveProgress();
     resetSendButtonToIdle();

     // Dispatch markdown prompt to AI Agent
     dispatchUserPrompt(markdownPrompt);
   }
   ```
3. The AI agent executes in the newly created session, reads the embedded directives, interacts with the filesystem, installs missing packages, and logs progress live into the chat viewport.

---

## 9. Implementation Acceptance Criteria (AC)

- [ ] **AC-1.6.3.9 (Settings Navigation)**: Settings Hub contains an "Export & Import" menu item that navigates to `#viewExportImport` with back-navigation support.
- [ ] **AC-1.6.3.10 (Touch Checkbox Selection)**: The Export view presents 5 checkboxes (`SSH Keys`, `API Keys`, `Custom Skills`, `MCP Servers`, `Packages`) and a "Select All / Deselect All" toggle.
- [ ] **AC-1.6.3.11 (Markdown Export Download)**: Tapping "Export Backup (.md)" triggers a browser download prompt with filename `AntigravityStudio-Backup-YYYYMMDD_HHMMSS.agy.md` and copies it to `/storage/emulated/0/Download/`.
- [ ] **AC-1.6.3.12 (Export Security Warning)**: The Export view displays a prominent security warning regarding unencrypted credentials in the exported Markdown.
- [ ] **AC-1.6.3.13 (File Type & Extension Check)**: Importing a non-Markdown file (e.g. `.json`, `.png`, `.bin`) is rejected with an inline error alert.
- [ ] **AC-1.6.3.14 (Magic Header & Signature Validation)**: Uploading an arbitrary `.md` file that lacks `<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->` or has an invalid generator frontmatter fails validation with a clear error banner.
- [ ] **AC-1.6.3.15 (Integrity & SHA-256 Check)**: If the Markdown body content has been altered, validation detects the SHA-256 checksum mismatch and aborts import.
- [ ] **AC-1.6.3.16 (Confirmation Sheet Preview)**: A valid file displays `#importConfirmSheet` with accurate item counts for SSH keys, API keys, skills, MCP servers, and packages.
- [ ] **AC-1.6.3.17 (New Session Initialization)**: Tapping "Start Setup Agent" closes Settings Hub, resets the active conversation ID, and submits the full Markdown content as the opening prompt.
- [ ] **AC-1.6.3.18 (Zero-Overwrite Invariant)**: The agent strictly respects existing configurations and does not overwrite existing SSH keys, API keys, skills, or MCP configurations.
- [ ] **AC-1.6.3.19 (UNIX Permission Enforcement)**: Restored private keys receive mode `0600`, public keys `0644`, and config files `0600`.

---

## 10. Gemini 4 Argon Architectural Review & Security Hardening

- **Auditor**: `argon-agent (high-reasoning)`
- **Overall Status**: **AUDITED & HARDENED (6 Architectural Mitigations Integrated)**

### Critical Mitigations Matrix
| Issue ID | Severity | Subsystem | Threat & Impact | Architectural Mitigation |
| :--- | :--- | :--- | :--- | :--- |
| **ARGON-V163-01** | HIGH | Credential Exposure | Exporting plaintext SSH private keys and API tokens in Markdown creates risk of accidental cloud sync or leaks. | Display explicit security warning banner above the export button. Instruct users that exports contain plaintext credentials. Maintain mode `0600` on staging files. |
| **ARGON-V163-02** | HIGH | Prompt Injection / Malicious Import | An attacker could craft a malicious `.md` file disguised as an export to execute arbitrary system destruction or exfiltration. | Enforce two-phase validation: Line 1 magic signature check, YAML frontmatter validation, SHA-256 checksum integrity verification, and explicit agent constraints limiting execution to idempotent provisioning. |
| **ARGON-V163-03** | MEDIUM | Data Overwrite Hazards | Importing an export on a machine with existing keys could clobber or wipe user credentials. | Hardcode the **Zero-Overwrite Invariant** into the agent directives: AI Agent must verify existence before creating any item and label collisions as `[PRESERVED]`. |
| **ARGON-V163-04** | MEDIUM | Denial of Service / File Flooding | User uploading a multi-gigabyte file could crash the WebView process via memory exhaustion. | Hardcap file upload size at 10 MB in JavaScript `FileReader` before reading into memory; reject larger files immediately. |
| **ARGON-V163-05** | LOW | Package Name Injection | Malformed package names in the packages section could attempt command injection during `apk add`. | Agent directives mandate strict package name sanitization (`^[a-zA-Z0-9_\-\.\+]+$`), passing arguments as discrete array parameters to `run_command` without shell interpolation. |
| **ARGON-V163-06** | MEDIUM | Checksum Invalidation | CRLF vs LF line ending translation across OS platforms (e.g. Windows/Android/Git) causes false checksum mismatch failures. | Normalize all payload line breaks (`payloadBody.replace(/\r\n/g, '\n').trim()`) before computing or verifying the SHA-256 digest in both Python and JavaScript. |
