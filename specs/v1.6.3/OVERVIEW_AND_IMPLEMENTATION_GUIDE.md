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
├── 01-settings-export-import-agent-provisioning.md # Settings UI, dual-phase validation, markdown schema
├── 02-local-configuration-restoration-engine.md   # Zero-leak local restore pipeline, apk progress streaming, dynamic warning visibility
├── 03-mandatory-password-encryption-bundle.md     # Mandatory AES-256-GCM + scrypt encryption, password UX, V1/V2 compatibility
└── OVERVIEW_AND_IMPLEMENTATION_GUIDE.md           # Master overview, architecture, and acceptance criteria
```

---

## 3. Implementation Acceptance Criteria (AC)

- [x] **AC-1.6.3.9 (Settings Navigation)**: Settings Hub contains an "Export & Import" menu item that navigates to `#viewExportImport` with back-navigation support and proper dismiss handling.
- [x] **AC-1.6.3.10 (Touch Checkbox Selection)**: The Export view presents 5 checkboxes (`SSH Keys`, `API Keys`, `Custom Skills`, `MCP Servers`, `Packages`) and a "Select All / Deselect All" toggle with a minimum 44px touch target.
- [x] **AC-1.6.3.11 (Direct Storage Export & Notification)**: Tapping "Export Backup (.md)" generates `AntigravityStudio-Backup-YYYYMMDD_HHMMSS.agy.md`, writes it directly to `/storage/emulated/0/Download/`, and notifies the user with an in-app toast.
- [x] **AC-1.6.3.12 (Export Security Warning)**: The Export view displays a prominent security warning regarding unencrypted credentials in the exported Markdown.
- [x] **AC-1.6.3.12b (Dynamic Export Warning Visibility)**: The security warning dynamically collapses/hides when neither SSH Keys nor API Keys are selected, and reappears when either is checked.
- [x] **AC-1.6.3.13 (File Type & Extension Check)**: Importing a non-Markdown file (e.g. `.json`, `.png`, `.bin`) is rejected with an inline error alert.
- [x] **AC-1.6.3.14 (Magic Header & Signature Validation)**: Uploading an arbitrary `.md` file that lacks `<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->` on Line 1 or has an invalid generator frontmatter fails validation with a clear error banner.
- [x] **AC-1.6.3.15 (Integrity & SHA-256 Check)**: The SHA-256 checksum is computed over the entire post-frontmatter payload body. Any tampering or truncation is detected and aborts import.
- [x] **AC-1.6.3.16 (Confirmation Sheet Preview)**: A valid file displays `#importConfirmSheet` with accurate item counts for SSH keys, API keys, skills, MCP servers, and packages, parsed from section blocks.
- [x] **AC-1.6.3.17 (Deterministic Local Restoration Engine)**: Tapping "Restore Configurations Locally" executes entirely within `server.py` without dispatching prompts or credentials to external LLM providers, avoiding privacy leaks and the fresh-install authentication catch-22.
- [x] **AC-1.6.3.18 (Zero-Overwrite Invariant)**: The local engine verifies existing files and does not overwrite existing SSH keys, API keys, skills, or MCP configurations, labeling them `[PRESERVED]`.
- [x] **AC-1.6.3.19 (UNIX Permission Enforcement)**: Restored private keys receive mode `0600`, public keys `0644`, directory `~/.ssh` receives `0700`, and config files receive `0600`.
- [x] **AC-1.6.3.20 (Recursive Skill Bundling & Extraction)**: Custom skills export and restore all constituent files (`scripts/`, `references/`), ensuring full functional integrity after restoration.
- [x] **AC-1.6.3.21 (API Keys Store Schema Envelope)**: The local engine merges restored keys into the `"keys"` object inside `~/.gemini/config/api_keys.json` with the standard versioned schema envelope and updates the API keys manifest.
- [x] **AC-1.6.3.22 (SSH Config & Directory Bootstrap)**: The local engine creates missing parent directories (`mkdir -p`) and reconciles `~/.ssh/config` `IdentityFile` entries for all restored keys.
- [x] **AC-1.6.3.23 (Live Streaming Progress for Packages)**: Missing packages trigger differential `apk add --no-cache` inside `APK_MUTATION_LOCK`, streaming real-time status to the UI during downloads and displaying a structured completion summary card.
- [x] **AC-1.6.3.24 (Mandatory Password Fields on Export)**: The Export subview provides password and confirm-password fields with show/hide toggles. The export button is strictly disabled until passwords match and have length >= 8.
- [x] **AC-1.6.3.25 (V2 Encrypted Envelope Generation)**: Tapping export generates a bundle with `<!-- ANTIGRAVITY_STUDIO_EXPORT_V2_ENCRYPTED -->`, valid scrypt parameters, salt, nonce, tag, and Base64-encoded AES-256-GCM ciphertext.
- [x] **AC-1.6.3.26 (Direct Storage Staging)**: The encrypted `.agy.md` file is saved to `/storage/emulated/0/Download/` containing zero plaintext credentials.
- [x] **AC-1.6.3.27 (V2 Detection & Decryption Bottom-Sheet)**: Picking an encrypted bundle automatically triggers `#importPasswordSheet` with export date and source version.
- [x] **AC-1.6.3.28 (Wrong Password Handling)**: Entering an incorrect password highlights the field in red, displays an inline error, leaves the sheet open for retry, and causes zero disk or state modifications.
- [x] **AC-1.6.3.29 (Successful Decryption & Restore Pipeline Hand-Off)**: Entering the correct password derives the key via `scrypt`, decrypts the payload in RAM, validates the inner SHA-256 checksum, and seamlessly invokes the local restoration engine.
- [x] **AC-1.6.3.30 (Legacy V1 Backward Compatibility)**: Uploading an unencrypted `V1` backup (`<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->`) displays a legacy notice and restores normally without requiring a password.
- [x] **AC-1.6.3.31 (Mobile Touch Standards & Zero-Latency)**: Password toggle buttons and modal actions adhere to 44px minimum touch targets and 300ms tap elimination rules.
- [x] **AC-1.6.3.32 (KDF Parameter Clamping & DoS Defense)**: Scrypt parameter values in frontmatter exceeding safety bounds ($N > 65536$, $r > 16$, $p > 4$) are rejected immediately before key derivation.
- [x] **AC-1.6.3.33 (Dual-Layer Integrity Verification)**: Outer SHA-256 of the Base64 ciphertext is verified prior to scrypt derivation; inner SHA-256 is verified after AEAD decryption.

---

## 4. Gemini 4 Argon Architectural Review & Security Hardening

- **Auditor**: `argon-agent (high-reasoning)`
- **Overall Status**: **AUDITED & HARDENED (27 Architectural Mitigations Integrated)**

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
| **ARGON-V163-11** | **HIGH** | Android SAF MIME Trap | Using `accept=".md,text/markdown"` causes Android SAF file picker to gray out `.md` files on devices lacking markdown MIME mapping. | Remove restrictive `accept` attribute and enforce `.md` validation in client JS; sanitize `MainActivity.kt` file chooser intent to `*/*`. |
| **ARGON-V163-12** | **MEDIUM** | Chat Viewport Privacy Leak | Dumping raw markdown prompt into chat bubble displays private keys on mobile screen; WebSocket protocol used `user_message` instead of `user_prompt`. | Use `user_prompt` and render a compact badge (`📦 Restoring Environment Bundle`) in chat; instruct agent never to echo private keys in output. |
| **ARGON-V163-13** | **MEDIUM** | SSH Config & Directory Bootstrap | Restoring keys without updating `~/.ssh/config` prevents Git from resolving non-default key identities; missing parent dirs cause write errors. | Mandate `mkdir -p` and append `IdentityFile` entries in `~/.ssh/config` for all restored keys. |
| **ARGON-V163-14** | **CRITICAL** | ZipSlip / Path Traversal in Skill Extraction | Malicious `.agy.md` bundle could define `#### File: ../../../.bashrc` to overwrite host files outside target skill directory. | Enforce `dest = (skill_dir / rel_path).resolve()`. Assert `dest.is_relative_to(skill_dir)`. Disallow leading slashes, backslashes, and `..` segments. |
| **ARGON-V163-15** | **HIGH** | SSH Key Name Injection | Key named `config`, `known_hosts`, or containing slashes could overwrite critical OpenSSH infrastructure. | Enforce `KEY_NAME_RE` (`^[a-zA-Z0-9_\-\.]+$`) and `name.lower() not in RESERVED_SSH_NAMES`. |
| **ARGON-V163-16** | **HIGH** | Import Concurrency Race Condition | Rapid double-taps on restore could dispatch overlapping imports, causing race conditions in `api_keys.json` and `apk` database. | Enforce an asynchronous `IMPORT_MUTATION_LOCK = asyncio.Lock()` in `server.py` and immediately disable the UI button on first tap. |
| **ARGON-V163-17** | **HIGH** | Package Command Injection | Malicious package names starting with `-` or containing flags could inject parameters into `apk add`. | Pass each package name through `validate_pkg_name()`. Reject names with whitespace, slashes, or leading dashes. Pass arguments solely as discrete array elements. |
| **ARGON-V163-18** | **MEDIUM** | Partial Failure Resiliency | If `apk add` fails due to lack of network, aborting entire restore gives false impression that keys also failed. | Decouple filesystem restoration from network package installation. If `apk add` fails, record in `packages.failed`, but report overall `success: true` with a clear partial warning. |
| **ARGON-V163-19** | **MEDIUM** | Stale Settings Hub Cache | Restoring configurations leaves Settings Hub views showing stale cached data until app restart. | On `import_configurations_result`, automatically dispatch WebSocket refresh commands (`list_ssh_keys`, `list_skills`, `list_mcp_servers`, `list_installed_packages`). |
| **ARGON-V163-20** | **MEDIUM** | Memory Flooding via Oversized Bundle | An oversized `.md` file could exhaust Termux heap memory during regex parsing. | Enforce a hard 10 MB ceiling in `server.py` (`len(content) <= 10 * 1024 * 1024`) prior to running regex parsing or SHA-256 computation. |
| **ARGON-V163-21** | **HIGH** | KDF / Resource Exhaustion | Attacker crafts a `.agy.md` bundle with excessive scrypt parameters (`kdf_n: 1048576`), causing Android OS OOM kill or freeze. | Strictly enforce parameter limits ($N \le 65536, r \le 16, p \le 4$) in `server.py` before invoking `hashlib.scrypt`. |
| **ARGON-V163-22** | **MEDIUM** | Android IME / Keyboard Leak | Android third-party keyboards cache text input from standard fields into cloud predictive dictionaries. | Set `autocomplete="new-password"` / `current-password`, `autocorrect="off"`, `autocapitalize="off"`, `spellcheck="false"`. |
| **ARGON-V163-23** | **HIGH** | CSPRNG Nonce Entropy | Pseudo-random or static nonces in AES-GCM allow catastrophic key recovery upon single collision. | Use `secrets.token_bytes(16)` for salt and `secrets.token_bytes(12)` for nonce; forbid user-supplied nonces. |
| **ARGON-V163-24** | **MEDIUM** | Premature CPU Burn | Computing memory-hard scrypt derivation on truncated or corrupted files wastes CPU and mobile battery. | Enforce outer SHA-256 integrity check against the Base64 ciphertext block *prior* to running scrypt key derivation. |
| **ARGON-V163-25** | **LOW** | Decryption Error Leakage | Exposing specific cryptographic exception messages (`InvalidTag` vs `ValueError`) leaks ciphertext structure. | Uniformly map all AEAD tag and ciphertext failures to `"Incorrect password or corrupted backup file."` |
| **ARGON-V163-26** | **HIGH** | Import Concurrency Race | User double-tapping "Decrypt & Restore" triggers concurrent scrypt derivations and package operations. | Immediately disable submit button on tap, show spinner, and serialize execution under `IMPORT_MUTATION_LOCK`. |
| **ARGON-V163-27** | **CRITICAL** | Monolith Python AST Class Scoping | Inserting helper functions inside `server.py` without proper indentation breaks class scope (`ARGON-08`). | Implement crypto utilities as pure module-level functions; verify AST via `scripts/verify_architectural_invariants.py`. |
