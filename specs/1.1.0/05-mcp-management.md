# Specification 05: Model Context Protocol (MCP) Server Integration

## 1. Overview & Problem Statement

The Model Context Protocol (MCP) enables AI agents to connect to external tools, databases, and enterprise services (e.g., PostgreSQL, GitHub MCP, Slack, Brave Search, Notion).

Antigravity natively supports MCP via its built-in CLI engine (`agy mcp`), storing configuration in `~/.gemini/config/mcp_config.json`. However:
1. Without explicit `--transport http` or `--transport stdio` flags, the `agy mcp add` CLI defaults to Stdio, mistakenly treating remote HTTP URLs as local executable binaries.
2. Synchronously executing `agy mcp list` during server queries can freeze the mobile UI for 10–30 seconds if a remote server is slow or unreachable.
3. Stdio MCP servers require Android Bionic environment variables (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `PATH`) to function correctly when spawned by the Glibc engine.
4. Users frequently attempt to configure MCP servers that rely on `npx` or `docker`, neither of which is pre-installed in the Android sandbox.
5. `mcp_config.json` contains sensitive authorization headers and environment tokens that must be protected with POSIX `0600` permissions and masked in UI listings.

### Core Objectives:
1. **Leverage Native CLI Engine with Explicit Transports**: Wrap `agy mcp` subcommands with explicit `--transport http` and `--transport stdio` flags, synchronized with `~/.gemini/config/mcp_config.json` (`0600`).
2. **Decoupled Fast Listing & Asynchronous Health Probes**: Read `mcp_config.json` directly in Python (`< 1ms`) to render UI lists with masked credentials, while running `agy mcp list` asynchronously with an 8-second timeout for status badges.
3. **Sandbox-Aware Stdio Environment**: Automatically populate mandatory runtime environment variables (`PATH`, `LD_LIBRARY_PATH`, `LD_PRELOAD`, `SSL_CERT_FILE`) for local Python MCP servers.
4. **Autonomous AI Setup Flow with Runtime Guidance**: Provide a visual configuration form and a `✨ Setup with AI` workflow that explicitly informs the agent of available local runtimes (`python3`, `bash`, `curl`, `git`) and warns against `npx`/`docker`.
5. **Lifecycle Management**: Support instant enable/disable toggling (`agy mcp enable/disable`), credential updates, and clean deletion.

---

## 2. Architecture & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Mobile User
    participant UI as MCP Servers View
    participant Srv as server.py
    participant CLI as agy mcp CLI
    participant Config as mcp_config.json (0600)
    participant Remote as Remote / Stdio MCP Server

    Note over User,UI: Fast Listing Flow (< 1ms)
    UI->>Srv: WebSocket: {"type": "list_mcp_servers"}
    Srv->>Config: Reads mcp_config.json directly
    Srv-->>UI: Emits server list with masked headers/tokens
    Srv->>CLI: Spawns async: agy mcp list (8s timeout)
    CLI-->>Srv: Real-time health status
    Srv-->>UI: Emits updated status badges

    Note over User,UI: Server Addition Flow
    User->>UI: Adds HTTP Server: "github-tools" (https://mcp.github.com/mcp)
    UI->>Srv: WebSocket: {"type": "add_mcp_server", "transport": "http", ...}
    Srv->>CLI: agy mcp add --transport http --scope user -H "Authorization: Bearer <token>" github-tools https://mcp.github.com/mcp
    CLI->>Config: Writes to ~/.gemini/config/mcp_config.json
    Srv->>Config: Enforces 0600 permissions
    Config-->>Srv: Success confirmation
    Srv-->>UI: Refreshes MCP Server List
```

---

## 3. Configuration Schema & Security

### File Topology:
* **Path**: `/data/data/ai.agy.app/files/home/.gemini/config/mcp_config.json`
* **POSIX Mode**: `0600` (`rw-------`).

### Schema Definition:
```json
{
  "mcpServers": {
    "github-mcp": {
      "serverUrl": "https://mcp.github.com/mcp",
      "headers": {
        "Authorization": "Bearer ghp_xxxxxxxxxxxxxxxxxxxx"
      },
      "disabled": false
    },
    "sqlite-helper": {
      "command": "python3",
      "args": ["/data/data/ai.agy.app/files/usr/share/mcp/sqlite_server.py"],
      "env": {
        "DB_PATH": "/storage/emulated/0/Documents/app.db",
        "PATH": "/data/data/ai.agy.app/files/usr/bin:/system/bin",
        "LD_LIBRARY_PATH": "/data/data/ai.agy.app/files/usr/lib",
        "LD_PRELOAD": "/data/data/ai.agy.app/files/usr/lib/libagy_shim.so",
        "SSL_CERT_FILE": "/data/data/ai.agy.app/files/usr/etc/tls/cert.pem"
      },
      "disabled": true
    }
  }
}
```

### Transport Types:
1. **Streamable HTTP (Remote Services)**:
   * **`serverUrl`**: Target URL (`https://.../mcp`).
   * **Headers**: Table of HTTP headers (e.g. `Authorization: Bearer <API_KEY>`). Masked in UI listings (`mask_api_key`).
2. **Stdio (Local Python Scripts & Binaries)**:
   * **`command`**: Executable path in sandbox (e.g. `python3`, `bash`).
   * **`args`**: Array of command-line arguments.
   * **`env`**: Process environment key-value pairs (auto-seeded with sandbox runtime paths). Note: `node`/`npx` and `docker` are not bundled in Android.

---

## 4. Backend Engine Implementation (`server.py`)

### Decoupled Fast Config Reading & Credential Masking
```python
def load_masked_mcp_servers() -> dict:
    config_path = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if not config_path.exists():
        atomic_write_secure(str(config_path), '{"mcpServers": {}}\n', mode=0o600)
        return {}

    os.chmod(str(config_path), 0o600)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    servers = data.get("mcpServers", {})
    masked_servers = {}
    for name, srv in servers.items():
        copy_srv = dict(srv)
        if "headers" in copy_srv and isinstance(copy_srv["headers"], dict):
            copy_srv["headers"] = {
                k: mask_api_key(v) for k, v in copy_srv["headers"].items()
            }
        if "env" in copy_srv and isinstance(copy_srv["env"], dict):
            copy_srv["env"] = {
                k: mask_api_key(v) if "KEY" in k or "TOKEN" in k or "SECRET" in k else v
                for k, v in copy_srv["env"].items()
            }
        masked_servers[name] = copy_srv
    return masked_servers
```

### Async Health Probe (`agy mcp list`)
```python
async def probe_mcp_health() -> dict[str, str]:
    cmd = [os.environ.get("AGY_BIN", "agy"), "mcp", "list"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        # Parse health status from CLI output
        status_map = {}
        for line in stdout.decode("utf-8", "replace").splitlines():
            # Extract server name and connection state
            match = re.search(r"(\S+)\s+(connected|ready|error|disabled)", line, re.I)
            if match:
                status_map[match.group(1)] = match.group(2).lower()
        return status_map
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("MCP health probe timed out or failed: %s", exc)
        return {}
```

---

## 5. Workflows

### A. Manual Server Setup:
1. User taps **`+ Add Server`** $\rightarrow$ **`Manual Configuration`**.
2. Inputs:
   * **Server Name**: Alphanumeric identifier (`^[a-zA-Z0-9_-]+$`).
   * **Transport Type**: `HTTP (Remote)` or `Stdio (Local)`.
   * If HTTP: Endpoint URL, Auth Type (None, API Key, Bearer Token), Token/Value.
   * If Stdio: Executable command (`python3`, `bash`), Arguments, Environment variables table.
3. User taps **"Test & Save"**:
   * For HTTP: executes `agy mcp add --transport http --scope user <name> <url> [ -H "Header: Val" ]`.
   * For Stdio: merges sandbox environment variables (`PATH`, `LD_PRELOAD`, etc.) and executes `agy mcp add --transport stdio --scope user ...`.
   * Enforces `0600` permissions on `mcp_config.json`.

### B. "Setup with AI" Flow:
1. User taps **`+ Add Server`** $\rightarrow$ **`✨ Setup with AI`**.
2. User provides natural language brief (e.g. *"Connect to Notion MCP at https://mcp.notion.com/v1 using my secret_abc"*).
3. Tapping **"Start AI Setup Session"**:
   * Switches to Chat Viewport and starts a session with the hardened prompt:
     ```text
     The user wants to configure an MCP server in Antigravity Studio for Android:
     Description: {user_brief}

     Environment Context:
     - Config path: ~/.gemini/config/mcp_config.json (must have 0600 permissions).
     - CLI tool: `agy mcp` (run `agy mcp --help` and `agy mcp add --help` to inspect exact flags). Always pass `--transport http` for remote URLs or `--transport stdio` for local commands.
     - Available local runtimes for Stdio MCP servers: `python3` (Python 3.14), `bash`, `curl`, `git`, `busybox`. Note that `node`/`npx` and `docker` are NOT pre-installed in this Android sandbox; prefer Streamable HTTP (`--transport http`) or Python-based (`python3`) MCP servers.
     - Pre-configured environment API keys available: {available_api_key_names}.

     Please configure this server, verify ~/.gemini/config/mcp_config.json is valid JSON with 0600 permissions, and test readiness with `agy mcp list`.
     ```

### C. Server Lifecycle Controls:
* **Enable / Disable Toggle**: Calls `agy mcp enable <name>` or `agy mcp disable <name>`.
* **Re-Authenticate**: Opens modal pre-populated with masked headers/env to replace expired keys.
* **Delete**: Prompts confirmation and calls `agy mcp remove <name>`.

---

## 6. Acceptance Criteria

- [ ] **AC-5.1: Native `agy mcp` Command Wrappers with Explicit Transports**
  - Verify `server.py` invokes `agy mcp add` with explicit `--transport http` or `--transport stdio` flags.
- [ ] **AC-5.2: Protected Config Storage & JSON Schema**
  - Verify `~/.gemini/config/mcp_config.json` exists, has `0600` permissions, and contains valid JSON with `"mcpServers"` root key:
    ```bash
    python3 -c '
    import json, os
    p = os.path.expanduser("~/.gemini/config/mcp_config.json")
    assert os.path.exists(p), "mcp_config.json missing"
    assert oct(os.stat(p).st_mode & 0o777) == "0o600", "mcp_config.json must be 0600"
    data = json.load(open(p))
    assert "mcpServers" in data and isinstance(data["mcpServers"], dict)
    print("MCP config gate PASSED")
    '
    ```
- [ ] **AC-5.3: Decoupled Fast Listing & Secret Masking**
  - Verify `GET /api/mcp/servers` returns in `< 50ms` by reading `mcp_config.json` directly with masked authorization headers and environment secrets.
- [ ] **AC-5.4: Stdio Sandbox Environment Injection**
  - Verify Stdio MCP servers receive required Android Bionic paths (`PATH`, `LD_LIBRARY_PATH`, `LD_PRELOAD`, `SSL_CERT_FILE`) in their environment block.
- [ ] **AC-5.5: AI Setup Handoff Flow with Android Sandbox Directives**
  - Verify `Setup with AI` initiates a chat session warning that `node`/`npx` is not pre-installed and instructing explicit transport flag usage.
