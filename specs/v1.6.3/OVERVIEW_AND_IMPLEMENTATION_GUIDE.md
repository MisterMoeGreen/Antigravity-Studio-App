# Master Overview & Implementation Guide: Antigravity Studio v1.6.3

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.6.3` (Settings Export/Import & Autonomous AI Agent Provisioning Bundle)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 10+ / API 29 through Android 15+)  
> **Status**: **DRAFT FOR ARGON ARCHITECTURAL AUDIT**

---

## 1. Executive Summary & Core Objectives

When migrating between Android devices or setting up a clean install of Antigravity Studio, users accumulate significant custom developer configurations:
1. SSH keys for remote Git operations.
2. API tokens for various intelligence providers.
3. Custom skills in `~/.gemini/config/skills/`.
4. MCP tool definitions in `~/.gemini/config/mcp_config.json`.
5. Alpine Linux packages installed in the PRoot container subsystem.

Manually entering these credentials and reconfiguring tools via touch screens creates severe user friction.

**Release `v1.6.3` delivers full environment portability via a self-provisioning Markdown bundle**:
1. **Granular Export Subview**: In Settings Hub (`#viewExportImport`), touch checkboxes let users select which subsystems to export.
2. **Deterministic Agent Instructions**: The generated `.md` file is self-documenting and includes explicit instructions, POSIX paths, and permission requirements (`0600`/`0700`/`0644`) for the Antigravity AI Agent.
3. **Mandatory Zero-Overwrite Invariant**: The agent must inspect existing configurations and never overwrite existing keys or skills, marking collisions as `[PRESERVED]`.
4. **Dual-Phase Import Validation**: Validates file format, magic header (`<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->`), and SHA-256 payload integrity before starting setup.
5. **1-Tap Autonomous Restoration**: Starts a clean chat session and submits the bundle prompt, enabling the AI agent to autonomously restore missing configurations and install container packages.

---

## 2. Specification Index

```text
specs/v1.6.3/
├── 01-settings-export-import-agent-provisioning.md # Settings UI, dual-phase validation, markdown schema, and agent directives
└── OVERVIEW_AND_IMPLEMENTATION_GUIDE.md           # Master overview, architecture, and acceptance criteria
```

---

## 3. Implementation Acceptance Criteria (AC)

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

## 4. Gemini 4 Argon Architectural Review & Security Hardening

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
| **ARGON-V163-06** | MEDIUM | Cross-Platform Checksum Mismatch | CRLF vs LF translation across OS platforms (Windows, Android, Git) breaks SHA-256 integrity checks. | Unconditionally normalize all payload line endings (`replace(/\r\n/g, '\n').trim()`) prior to computing or validating the SHA-256 digest in both frontend and backend. |
