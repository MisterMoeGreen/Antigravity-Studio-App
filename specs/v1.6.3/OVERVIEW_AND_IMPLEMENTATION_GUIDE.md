# Master Overview & Implementation Guide: Antigravity Studio v1.6.3

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.6.3` (Settings Export/Import & Autonomous AI Agent Provisioning Bundle)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 10+ / API 29 through Android 15+)  
> **Status**: **APPROVED & HARDENED BY ARGON ARCHITECTURAL AUDIT**

---

## 1. Executive Summary & Core Objectives

When migrating between Android devices or setting up a clean install of Antigravity Studio, users accumulate significant custom developer configurations:
1. SSH keys for remote Git operations.
2. API tokens for various intelligence providers.
3. Custom skills in `~/.gemini/config/skills/` (including scripts and documentation references).
4. MCP tool definitions in `~/.gemini/config/mcp_config.json`.
5. Alpine Linux packages installed in the PRoot container subsystem.

Manually entering these credentials and reconfiguring tools via touch screens creates severe user friction.

**Release `v1.6.3` delivers full environment portability via a self-provisioning Markdown bundle**:
1. **Granular Export Subview**: In Settings Hub (`#viewExportImport`), touch checkboxes let users select which subsystems to export.
2. **Deterministic Agent Instructions**: The generated `.md` file is self-documenting and includes explicit instructions, POSIX paths, and permission requirements (`0600`/`0700`/`0644`) for the Antigravity AI Agent.
3. **Mandatory Zero-Overwrite Invariant**: The agent must inspect existing configurations and never overwrite existing keys or skills, marking collisions as `[PRESERVED]`.
4. **Dual-Phase Import Validation**: Validates file format, magic header (`<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->`), and SHA-256 payload integrity before starting setup.
5. **1-Tap Autonomous Restoration**: Starts a clean chat session and submits the bundle prompt, enabling the AI agent to autonomously restore missing configurations and install container packages.
6. **Recursive Multi-File Skill Preservation**: Skill directories are recursively scanned to include all constituent scripts (`scripts/*.py`) and references (`references/*.md`), preventing broken custom skills.
7. **Privacy & UX Protection**: Chat viewport displays a compact summary card rather than dumping raw private keys on screen, and the agent is forbidden from echoing sensitive tokens in responses.

---

## 2. Specification Index

```text
specs/v1.6.3/
├── 01-settings-export-import-agent-provisioning.md # Settings UI, dual-phase validation, markdown schema, and agent directives
└── OVERVIEW_AND_IMPLEMENTATION_GUIDE.md           # Master overview, architecture, and acceptance criteria
```

---

## 3. Implementation Acceptance Criteria (AC)

- [ ] **AC-1.6.3.9 (Settings Navigation)**: Settings Hub contains an "Export & Import" menu item that navigates to `#viewExportImport` with back-navigation support and proper dismiss handling.
- [ ] **AC-1.6.3.10 (Touch Checkbox Selection)**: The Export view presents 5 checkboxes (`SSH Keys`, `API Keys`, `Custom Skills`, `MCP Servers`, `Packages`) and a "Select All / Deselect All" toggle with a minimum 44px touch target.
- [ ] **AC-1.6.3.11 (Direct Storage Export & Notification)**: Tapping "Export Backup (.md)" generates `AntigravityStudio-Backup-YYYYMMDD_HHMMSS.agy.md`, writes it directly to `/storage/emulated/0/Download/`, and notifies the user with an in-app toast.
- [ ] **AC-1.6.3.12 (Export Security Warning)**: The Export view displays a prominent security warning regarding unencrypted credentials in the exported Markdown.
- [ ] **AC-1.6.3.13 (File Type & Extension Check)**: Importing a non-Markdown file (e.g. `.json`, `.png`, `.bin`) is rejected with an inline error alert.
- [ ] **AC-1.6.3.14 (Magic Header & Signature Validation)**: Uploading an arbitrary `.md` file that lacks `<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->` on Line 1 or has an invalid generator frontmatter fails validation with a clear error banner.
- [ ] **AC-1.6.3.15 (Integrity & SHA-256 Check)**: The SHA-256 checksum is computed over the entire post-frontmatter payload body. Any tampering or truncation is detected and aborts import.
- [ ] **AC-1.6.3.16 (Confirmation Sheet Preview)**: A valid file displays `#importConfirmSheet` with accurate item counts for SSH keys, API keys, skills, MCP servers, and packages, parsed from section blocks.
- [ ] **AC-1.6.3.17 (New Session Initialization & Privacy Shield)**: Tapping "Start Setup Agent" closes Settings Hub, resets the active conversation ID, dispatches `{ type: "user_prompt" }`, and renders a compact card badge (`📦 Restoring Environment Bundle`) in the chat bubble.
- [ ] **AC-1.6.3.18 (Zero-Overwrite Invariant)**: The agent strictly respects existing configurations and does not overwrite existing SSH keys, API keys, skills, or MCP configurations, labeling them `[PRESERVED]`.
- [ ] **AC-1.6.3.19 (UNIX Permission Enforcement)**: Restored private keys receive mode `0600`, public keys `0644`, and config files `0600`.
- [ ] **AC-1.6.3.20 (Recursive Skill Bundling)**: Custom skills export all constituent files (`scripts/`, `references/`), ensuring full functional integrity after restoration.
- [ ] **AC-1.6.3.21 (API Keys Store Schema Envelope)**: The agent directives mandate updating the `"keys"` object inside `~/.gemini/config/api_keys.json` with the standard versioned schema envelope.
- [ ] **AC-1.6.3.22 (SSH Config & Directory Bootstrap)**: The agent directives mandate creating missing parent directories (`mkdir -p`) and reconciling `~/.ssh/config` `IdentityFile` entries for all restored keys.

---

## 4. Gemini 4 Argon Architectural Review & Security Hardening

- **Auditor**: `argon-agent (high-reasoning)`
- **Overall Status**: **AUDITED & HARDENED (13 Architectural Mitigations Integrated)**

### Critical Mitigations Matrix

| Issue ID | Severity | Subsystem | Threat & Impact | Architectural Mitigation |
| :--- | :--- | :--- | :--- | :--- |
| **ARGON-V163-01** | HIGH | Credential Exposure | Exporting plaintext SSH private keys and API tokens in Markdown creates risk of accidental cloud sync or leaks. | Display explicit security warning banner above the export button. Instruct users that exports contain plaintext credentials. Maintain mode `0600` on staging files. |
| **ARGON-V163-02** | HIGH | Prompt Injection / Malicious Import | An attacker could craft a malicious `.md` file disguised as an export to execute arbitrary system destruction or exfiltration. | Enforce two-phase validation: Line 1 magic signature check, YAML frontmatter validation, SHA-256 checksum integrity verification, and explicit agent constraints limiting execution to idempotent provisioning. |
| **ARGON-V163-03** | MEDIUM | Data Overwrite Hazards | Importing an export on a machine with existing keys could clobber or wipe user credentials. | Hardcode the **Zero-Overwrite Invariant** into the agent directives: AI Agent must verify existence before creating any item and label collisions as `[PRESERVED]`. |
| **ARGON-V163-04** | MEDIUM | Denial of Service / File Flooding | User uploading a multi-gigabyte file could crash the WebView process via memory exhaustion. | Hardcap file upload size at 10 MB in JavaScript `FileReader` before reading into memory; reject larger files immediately. |
| **ARGON-V163-05** | LOW | Package Name Injection | Malformed package names in the packages section could attempt command injection during `apk add`. | Agent directives mandate strict package name sanitization (`^[a-zA-Z0-9_\-\.\+]+$`), passing arguments as discrete array parameters to `run_command` without shell interpolation. |
| **ARGON-V163-06** | MEDIUM | Checksum Line-Ending Drift | CRLF vs LF translation across OS platforms (Windows, Android, Git) causes false SHA-256 checksum failures. | Unconditionally normalize all payload line endings (`replace(/\r\n/g, '\n').trim()`) prior to computing or validating the SHA-256 digest in both frontend and backend. |
| **ARGON-V163-07** | **CRITICAL** | SHA-256 Payload Mismatch | Python computed hash over sections only, but JavaScript hashed post-frontmatter body including preamble, causing 100% verification failure. | Python and JavaScript both compute SHA-256 over the entire body following the closing frontmatter delimiter `---`. |
| **ARGON-V163-08** | **CRITICAL** | Multi-File Skill Stripping | Exporting only `SKILL.md` drops `scripts/` and `references/`, breaking multi-file skills like `argon-agent` and `skoda-control`. | Recursively traverse skill directories and bundle all constituent files preserving relative file paths. |
| **ARGON-V163-09** | **HIGH** | API Keys Store Corruption | Flat JSON structure in prompt could lead agent to overwrite `api_keys.json` without the `{"version": 1, "updated_at": ..., "keys": {...}}` envelope, wiping all keys. | Explicitly mandate the envelope schema and instruct agent to merge into the `"keys"` dictionary. |
| **ARGON-V163-10** | **HIGH** | Confirmation Tally Errors | Regex matching `### MCP Server:` failed because MCPs are emitted as a JSON dictionary; `"value":` matched globally across skills. | Parse specific section blocks (`JSON.parse` the fenced block in Section: MCP Servers and API Keys) to extract true item counts. |
| **ARGON-V163-11** | **HIGH** | Android SAF MIME Trap | Using `accept=".md,text/markdown"` causes Android SAF file picker to gray out `.md` files on devices lacking markdown MIME mapping. | Broaden `accept` to include `.md,text/markdown,text/plain,application/octet-stream,*/*` and enforce the `.md` extension in client JS. |
| **ARGON-V163-12** | **MEDIUM** | Chat Viewport Privacy Leak | Dumping raw markdown prompt into chat bubble displays private keys on mobile screen; WebSocket protocol used `user_message` instead of `user_prompt`. | Use `user_prompt` and render a compact badge (`📦 Restoring Environment Bundle`) in chat; instruct agent never to echo private keys in output. |
| **ARGON-V163-13** | **MEDIUM** | SSH Config & Directory Bootstrap | Restoring keys without updating `~/.ssh/config` prevents Git from resolving non-default key identities; missing parent dirs cause write errors. | Mandate `mkdir -p` and append `IdentityFile` entries in `~/.ssh/config` for all restored keys. |
