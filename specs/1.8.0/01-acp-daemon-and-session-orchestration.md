# Module 01: ACP Daemon Lifecycle & Session Orchestration

## 1. Daemon Lifecycle Architecture

### 1.1 Process Management
In Antigravity Studio v1.8.0, the Python backend daemon (`server.py`) maintains a single long-lived subprocess running `agy acp`:

```bash
agy acp --dangerously-skip-permissions
```

* **Standard Input/Output Transport**: Communication occurs over bidirectional JSON-RPC 2.0 lines terminated by newline (`\n`).
* **Auto-Restart & Health Supervision**: If the `agy acp` process crashes or terminates unexpectedly, `AgyProcessOrchestrator` automatically captures the exit code, logs the stack trace, and respawns the daemon with exponential backoff.
* **Environment Injection**: The daemon process inherits the full app environment, including:
  * `AGY_AUTO_UPDATE=1`
  * `GIT_SSH_COMMAND="/data/data/ai.agy.app/files/usr/bin/ssh -F /data/data/ai.agy.app/files/home/.ssh/config"`
  * User-configured API keys loaded from `~/.gemini/config/api_keys.json`.

### 1.2 Android Subsystem Concurrency: Why `--process-per-session` is Avoided
The upstream CLI provides a flag `--process-per-session` that spawns an isolated worker process for each session ID. 

> [!CAUTION]
> **Android Anonymous ELF Loader Restriction**:
> On Android with W^X enforcement and PRoot containerization, executing `agy acp --process-per-session` fails immediately with:
> `Failed to start a Antigravity process for the session: write |1: broken pipe`
> because internal sub-process worker pipe spawning fails under the anonymous-memory ELF bridge.

**The Solution**: Standardize on **single-process multiplexing**.
Empirical verification confirms that a single `agy acp` daemon process cleanly manages multiple concurrent sessions (`s1`, `s2`, ...) in parallel over stdio. Every notification and response includes the originating `sessionId`, allowing `server.py` to route traffic accurately with zero IPC pipe failures.

---

## 2. Connection Initialization (`initialize`)

Upon daemon startup, `server.py` immediately issues the `initialize` handshake:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientInfo": {
      "name": "AntigravityStudio",
      "version": "1.8.0"
    },
    "capabilities": {
      "loadSession": true,
      "sessionCapabilities": {
        "resume": {},
        "close": {},
        "load": {},
        "list": {}
      }
    }
  }
}
```

### Verified Server Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentCapabilities": {
      "loadSession": true,
      "load_session": true,
      "promptCapabilities": {
        "image": true,
        "audio": false,
        "embeddedContext": true
      },
      "sessionCapabilities": {
        "resume": {},
        "close": {},
        "load": {},
        "list": {},
        "additionalDirectories": {}
      },
      "auth": { "logout": {} },
      "_meta": {
        "antigravity": {
          "tier": true,
          "systemPrompt": { "append": true }
        }
      }
    },
    "agentInfo": {
      "name": "Antigravity ACP Server",
      "version": "1.5.1"
    },
    "authMethods": [
      { "id": "oauth-personal", "name": "Log in with Google" },
      { "id": "oauth-business", "name": "Log in with Gemini Enterprise" },
      { "id": "gemini-api-key", "name": "Gemini API key" },
      { "id": "agent-platform", "name": "Gemini Enterprise Agent Platform" }
    ]
  }
}
```

---

## 3. Session Lifecycle Management

### 3.1 Creating a New Session (`session/new`)
When a user taps "+ New Chat" in the mobile drawer, the frontend requests a session:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "workspaceUris": ["file:///storage/emulated/0/Documents/AgyStudio"]
  }
}
```

The response returns:
* `sessionId`: Persistent UUID (e.g. `deb25aad-8028-4d55-aba6-7ed3988f1b03`).
* `models.availableModels`: Complete catalog of available models.
* `configOptions`: Configurable options for `model`, `thought_level`, and `mode`.
* `_meta.antigravity.tier`: User subscription tier (e.g. `Google AI Pro`).

### 3.2 Loading an Existing Session (`session/load` & `session/resume`)
When the user switches sessions from the conversation list, or after the app is restarted, `server.py` re-attaches to the session using `session/load`:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/load",
  "params": {
    "sessionId": "2efe11fc-4bad-45e9-bcb0-7db85a9d8ec2"
  }
}
```

* **Persistence Compatibility**: Verified that ACP automatically syncs all sessions, previews, and turn titles to SQLite `conversation_summaries.db`.
* **Zero History Replay**: Unlike batch mode which had to read past files off disk on every turn, `session/load` mounts the conversation directly in ACP's state machine without replaying turns to the terminal.

### 3.3 Prompt Caching Across Turns
In multi-turn sessions, context is retained in memory by the ACP engine. In our benchmarks:
* **Turn 1**: Loaded 13,369 prompt tokens.
* **Turn 2**: Reused **11,319 cached tokens** (`cachedReadTokens: 11319`), delivering response start times under 300ms.

---

## 4. Dynamic Model & Reasoning Effort Selection

In the legacy architecture, switching models or reasoning effort required passing `--model <name>` and `--effort <level>` command-line flags, which forced killing or restarting the active process.

In v1.8.0, model and effort changes are applied dynamically in-flight using `session/set_config_option`:

### 4.1 Switching Model
```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "session/set_config_option",
  "params": {
    "sessionId": "deb25aad-8028-4d55-aba6-7ed3988f1b03",
    "configId": "model",
    "value": "gemini-3.8-flash"
  }
}
```

### 4.2 Switching Reasoning Effort (Thought Level)
```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "session/set_config_option",
  "params": {
    "sessionId": "deb25aad-8028-4d55-aba6-7ed3988f1b03",
    "configId": "thought_level",
    "value": "high"
  }
}
```

### 4.3 Switching Agent Execution Mode
```json
{
  "jsonrpc": "2.0",
  "id": 12,
  "method": "session/set_config_option",
  "params": {
    "sessionId": "deb25aad-8028-4d55-aba6-7ed3988f1b03",
    "configId": "mode",
    "value": "accept-edits"
  }
}
```

The daemon emits a `session/update` notification (`config_option_update`) immediately, broadcasting the confirmed configuration to all connected mobile UI clients without interrupting background tasks.
