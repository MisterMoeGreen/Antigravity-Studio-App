# Specification 03: API Key & Environment Credential Management

## 1. Overview & Problem Statement

Modern agentic workflows and tool integrations rely on API keys (e.g., `GITHUB_TOKEN` for repository and PR access, `OPENAI_API_KEY` for cross-model tools, `TAVILY_API_KEY` for live web searches, `HUGGINGFACE_TOKEN` for model downloads).

Without a structured credential management system:
1. Users are forced to paste raw API keys into chat prompts repeatedly.
2. The agent has no standard way to access secrets when running Python scripts or shell tools.
3. Raw secrets risk being leaked into conversation transcripts, logs, and LLM context history if printed by tools like `curl -v` or `env`.
4. Overwriting critical system environment variables (such as `LD_PRELOAD`, `PATH`, or `GODEBUG`) would compromise the Bionic/Glibc runtime sandbox.

### Core Objectives:
1. **Protected Storage**: Store user API keys in a protected configuration store (`api_keys.json` with `0600` permissions on internal storage).
2. **System Variable Denylist**: Reject attempts to store or overwrite reserved system environment variables (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `PATH`, `HOME`, `GODEBUG`, etc.).
3. **Seamless Environment Injection**: Inject keys into the process environment (`proc_env`) of `agy` and its subprocesses so all tools, scripts, and MCP servers have immediate access.
4. **Secret Masking & Output Redaction**: Display only masked key values (`ghp_••••••••3a9b`, with keys `<= 8` chars fully masked as `••••••••`), and redact active secret values from tool output streams before broadcasting over WebSocket.
5. **Decoupled LLM Awareness**: Automatically maintain an API Key Manifest in `~/.gemini/config/rules/api_keys_manifest.md` informing the model of available keys without prepending redundant text to individual user turns or exposing raw values in system prompts.

---

## 2. Architecture & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Mobile User
    participant UI as API Keys View
    participant Srv as server.py Daemon
    participant Store as api_keys.json (0600)
    participant Manifest as api_keys_manifest.md (0600)
    participant Agy as agy Subprocess
    participant Tool as Tool / Python / Curl / MCP

    User->>UI: Adds Key: GITHUB_TOKEN = ghp_abc123...
    UI->>Srv: WebSocket: {"type": "save_api_key", "name": "GITHUB_TOKEN", "value": "ghp_...", "desc": "GitHub PAT"}
    Srv->>Srv: Validates name against PROTECTED_ENV_NAMES
    Srv->>Store: Writes to ~/.gemini/config/api_keys.json (0600)
    Srv->>Manifest: Syncs ~/.gemini/config/rules/api_keys_manifest.md (0600)
    Srv-->>UI: Emits key list with masked value: "ghp_••••••••3a9b"
    
    User->>UI: Submits Prompt: "Search GitHub for PRs and clone the repo"
    Srv->>Store: Reads active API keys
    Srv->>Srv: Injects keys into proc_env (proc_env["GITHUB_TOKEN"] = "ghp_abc123...")
    Srv->>Agy: Spawns agy with proc_env (auto-discovers api_keys_manifest.md)
    Agy->>Tool: Executes: curl -H "Authorization: Bearer $GITHUB_TOKEN"
    Tool-->>Agy: GitHub API 200 OK
    Agy-->>Srv: Output stream (scrubbed with redact_secrets_from_text)
    Srv-->>UI: Streamed response without raw token leakage
```

---

## 3. Storage Schema & Security

### File Topology:
* **Path**: `/data/data/ai.agy.app/files/home/.gemini/config/api_keys.json`
* **POSIX Mode**: `0600` (`rw-------`) on internal storage.

### Schema Definition:
```json
{
  "version": 1,
  "updated_at": 1740000000,
  "keys": {
    "GITHUB_TOKEN": {
      "value": "ghp_1234567890abcdef1234567890abcdef",
      "description": "GitHub Personal Access Token",
      "created_at": 1740000000
    },
    "TAVILY_API_KEY": {
      "value": "tvly-abcdef1234567890abcdef1234567890",
      "description": "Tavily web search engine API",
      "created_at": 1740000000
    }
  }
}
```

---

## 4. Protected Environment Variable Denylist & Masking

### System Variable Denylist (`server.py`)
To prevent corruption of linker paths, sandbox binaries, or root directories, attempts to set protected system variables are strictly rejected:
```python
PROTECTED_ENV_NAMES = {
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG",
    "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP",
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
    "GODEBUG", "GOMAXPROCS", "GOPATH", "GOROOT",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR",
    "BASH_ENV", "ENV", "IFS", "PROMPT_COMMAND", "LANG", "LC_ALL",
    "ANDROID_DATA", "ANDROID_ROOT", "ANDROID_STORAGE", "EXTERNAL_STORAGE"
}
API_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

def validate_api_key_name(name: str) -> None:
    if not API_KEY_NAME_RE.match(name):
        raise ValueError("Key name must start with A-Z and contain only A-Z, 0-9, and _")
    if name in PROTECTED_ENV_NAMES or name.startswith(("LD_", "DYLD_", "ANDROID_", "AGY_")):
        raise ValueError(f"'{name}' is a protected system environment variable and cannot be overridden")
```

### Safe Secret Masking
```python
def mask_api_key(value: str) -> str:
    n = len(value)
    if n <= 8:
        return "••••••••"
    if n <= 14:
        return f"{value[:2]}••••••••{value[-2:]}"
    return f"{value[:4]}••••••••{value[-4:]}"
```

### Output Stream Redaction
To protect against accidental leakage when tools run with verbose output (`curl -v`, `env`):
```python
def redact_secrets_from_text(text: str, active_keys: dict[str, str]) -> str:
    if not text or not active_keys:
        return text
    for key_name, raw_val in active_keys.items():
        if raw_val and len(raw_val) >= 6 and raw_val in text:
            text = text.replace(raw_val, f"[REDACTED:{key_name}]")
    return text
```

---

## 5. Decoupled LLM Awareness: The API Key Manifest Rule

Instead of polluting individual user turn messages with repeated markdown blocks, `server.py` automatically synchronizes an **API Key Manifest** to `/data/data/ai.agy.app/files/home/.gemini/config/rules/api_keys_manifest.md` (`0600`) whenever `api_keys.json` is modified:

```markdown
# Environment API Keys Available

The following API keys are pre-configured in your process environment:
- `GITHUB_TOKEN`: GitHub Personal Access Token
- `TAVILY_API_KEY`: Tavily web search engine API

You can access these directly via `$KEY_NAME` in shell scripts or `os.environ["KEY_NAME"]` in Python scripts.
CRITICAL: Do NOT ask the user to provide these keys, and NEVER echo, print, or run `curl -v` in a way that outputs raw key values to stdout/stderr.
```

Because `api_keys_manifest.md` resides in `~/.gemini/config/rules/`, `libagy.so` automatically loads it into the model's System Rules Context without token duplication.

---

## 6. Backend Service API (`server.py`)

### WebSocket Endpoints:
1. **`list_api_keys`**:
   - Returns key names, descriptions, creation timestamps, and safe masked previews (`mask_api_key`).
2. **`save_api_key(name, value, description)`**:
   - Validates name using `validate_api_key_name()`.
   - Writes to `api_keys.json` atomically via `atomic_write_secure()` (`0600`).
   - Regenerates `~/.gemini/config/rules/api_keys_manifest.md` (`0600`).
3. **`get_api_key_raw(name)`**:
   - Used strictly when user taps "Copy to Clipboard" in the UI.
   - Emits raw value strictly in an ephemeral response to the requesting client.
4. **`delete_api_key(name)`**:
   - Removes entry from `api_keys.json` and refreshes `api_keys_manifest.md`.

---

## 7. Acceptance Criteria

- [ ] **AC-3.1: Protected Storage & Permissions**
  - Verify `api_keys.json` is created with `0600` permissions on internal storage.
- [ ] **AC-3.2: Subprocess Environment Injection**
  - Verify that keys stored in `api_keys.json` are present in `proc_env` when `agy` executes shell commands and Python tools.
- [ ] **AC-3.3: LLM Context Inventory Manifest Synchronization**
  - Verify `~/.gemini/config/rules/api_keys_manifest.md` is updated atomically (`0600`) when keys change, and raw secret values are excluded from system rules.
- [ ] **AC-3.4: Masked UI Display & Stream Redaction**
  - Verify that listing responses return masked string values (with keys `<= 8` chars masked as `••••••••`), and `redact_secrets_from_text` replaces raw tokens in output streams.
- [ ] **AC-3.5: One-Tap Clipboard Copy & Delete**
  - Verify keys can be safely retrieved for copying to the Android clipboard, and deleted cleanly from disk.
- [ ] **AC-3.6: Protected Environment Variable Denylist**
  - Verify `save_api_key` rejects attempts to save keys named `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PATH`, or `HOME`.
