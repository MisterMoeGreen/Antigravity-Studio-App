# Specification 01: Multi-Session Concurrency & Process Orchestrator

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.4.0` (Parallel Session Concurrency & Background Execution)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)  
> **Branch**: `v1.4.0`  
> **Review Status**: **ARCHITECTURALLY AUDITED & HARDENED BY GEMINI 4 ARGON (`ARGON-V14-09`, `ARGON-V14-12`, `ARGON-V14-13`, `ARGON-V14-14`, `ARGON-V14-16`)**

---

## 1. Problem Statement & Root Cause

In `v1.0.0` through `v1.3.4`, Antigravity Studio operates with a strictly serialized, single-session process orchestrator model in [`agy-ui/server.py`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/server.py):

1. **Global Process Lock**:
   `AgyProcessOrchestrator` maintains a single global lock `self._turn_lock = asyncio.Lock()`. When a user submits a prompt in Session A, this lock is acquired for the entire duration of the turn. If the user navigates to Session B and attempts to submit a prompt, the server abruptly rejects it with `"Busy: Antigravity is currently processing a turn."`
2. **Global Config & Process Mutation**:
   The orchestrator maintains singleton fields:
   - `self.config: SessionConfig` (holding single `conversation_id`, `model`, `workspace`)
   - `self.active_proc: Optional[asyncio.subprocess.Process]`
   - `self.is_running: bool`
   - `self._current_turn_id: Optional[str]`
   When the user switches sessions (`mtype == "switch_session"`), mutating `self.orchestrator.config.conversation_id` clobbers the active conversation pointer in-flight.
3. **Missing Session Metadata in Event Streams**:
   Streaming events emitted by `_demux_json_event` and `_consume_stdout_stream` (`agent_token`, `agent_thought`, `tool_call`) include only `turn_id` and omit `conversation_id`. Consequently, the frontend cannot distinguish which session owns incoming tokens.
4. **Clobbered Completion Frames**:
   When Session A completes while the user is viewing Session B, `turn_complete` broadcasts Session B's ID for Session A's completion!
5. **Orphaned Descendants on Abort (`ARGON-V14-09`)**:
   `proc.terminate()` signals only the top-level `agy` PID. Child PRoot tracees, guest compilers, and subshells survive as background orphans.

---

## 2. Core Objectives

1. **Independent Session Execution Contexts**: Transition `AgyProcessOrchestrator` from a single-process singleton to a multi-session registry:
   ```python
   self._sessions: dict[str, SessionExecutionContext]
   ```
   Where each conversation possesses its own `turn_lock`, `active_proc`, `current_turn_id`, `is_running`, `workspace`, and streaming tasks.
2. **True Parallel Execution**: Allow users to trigger turns concurrently across multiple conversations without blocking or clobbering one another.
3. **Session-Tagged Streaming Protocol**: Enforce that every streaming frame (`turn_start`, `agent_token`, `agent_thought`, `tool_call`, `turn_complete`, `session_status`, `ask_question`) explicitly carries `conversation_id`.
4. **Process Group Signal Isolation (`ARGON-V14-09`)**:
   Spawn `agy` with `start_new_session=True` (`setsid`) and use `os.killpg(proc.pid, signal.SIGINT)` / `SIGKILL` to reliably clean up all child PRoot processes.
5. **SQLite WAL & Workspace Mutex (`ARGON-V14-12`, `ARGON-V14-13`)**:
   Enforce persistent WAL mode on SQLite databases with 10s busy timeouts, and prevent concurrent turns from running against the exact same workspace directory.
6. **Mobile Concurrency & RAM Guard (`ARGON-V14-16`)**:
   Cap concurrent sessions at `MAX_CONCURRENT_SESSIONS = 2` and require `/proc/meminfo` `MemAvailable >= 750 MB` before permitting a second parallel turn.

---

## 3. Data Architecture & Context Registry

### 3.1 `SessionExecutionContext` Data Model

```python
@dataclass
class TurnSnapshot:
    turn_id: str
    model: str
    raw_markdown: str = ""
    raw_thoughts: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

@dataclass
class SessionExecutionContext:
    conversation_id: str
    workspace: str
    model: str = "gemini-3.8-flash"
    mode: str = "default"
    effort: str = "high"
    active_proc: Optional[asyncio.subprocess.Process] = None
    current_turn_id: Optional[str] = None
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_running: bool = False
    interrupted: bool = False
    waiting_input: bool = False
    pending_question: Optional[dict[str, Any]] = None
    active_snapshot: Optional[TurnSnapshot] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_thinking_tokens: int = 0
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
```

---

## 4. Subprocess Spawning & Process Group Lifecycle

```python
# In AgyProcessOrchestrator.execute_turn():
cmd = self.build_cli_args(ctx, prompt=prompt)

proc_env = dict(os.environ)
proc_env["AGY_AUTO_UPDATE"] = "1"
proc_env["GIT_SSH_COMMAND"] = f"{USR_DIR}/bin/ssh -F {SSH_DIR}/config"
proc_env["AGY_WEB_PORT"] = str(PORT)
proc_env["AGY_CONVERSATION_ID"] = ctx.conversation_id
proc_env["AGY_TURN_ID"] = turn_id

# ARGON-V14-09: start_new_session=True runs setsid() in child, creating isolated PGID
ctx.active_proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=ctx.workspace if Path(ctx.workspace).is_dir() else str(HOME_DIR),
    env=proc_env,
    limit=16 * 1024 * 1024,
    start_new_session=True,
)
```

### 4.1 Process Group Termination (`abort_turn`)

```python
async def abort_turn(self, conversation_id: Optional[str] = None) -> bool:
    target_cid = conversation_id or self.active_view_cid
    if not target_cid or target_cid not in self._sessions:
        return False
    ctx = self._sessions[target_cid]
    proc = ctx.active_proc
    if not proc or proc.returncode is not None:
        return False

    ctx.interrupted = True
    pgid = proc.pid  # Because start_new_session=True sets PGID == PID
    try:
        os.killpg(pgid, signal.SIGINT)
    except ProcessLookupError:
        return True

    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True
```

---

## 5. Concurrency & Resource Guardrails

1. **Workspace Collision Guard (`ARGON-V14-13`)**:
   ```python
   norm_ws = os.path.realpath(ctx.workspace)
   for other_cid, other_ctx in self._sessions.items():
       if other_cid != conversation_id and other_ctx.is_running:
           if os.path.realpath(other_ctx.workspace) == norm_ws:
               await self.broadcast({
                   "type": "system_card",
                   "conversation_id": conversation_id,
                   "title": "Workspace In Use",
                   "markdown": (
                       f"Session `{other_cid[:8]}` is currently modifying workspace "
                       f"`{ctx.workspace}`. To prevent Git lock and file corruption, "
                       f"wait for it to complete or switch to another directory."
                   ),
                   "level": "warning"
               })
               return
   ```
2. **Dynamic RAM Ceiling (`ARGON-V14-16`)**:
   ```python
   def get_available_ram_mb() -> int:
       try:
           with open("/proc/meminfo", "r", encoding="utf-8") as f:
               for line in f:
                   if line.startswith("MemAvailable:"):
                       return int(line.split()[1]) // 1024
       except Exception:
           pass
       return 2048

   running_count = sum(1 for c in self._sessions.values() if c.is_running)
   if running_count >= 1 and get_available_ram_mb() < 750:
       await self.broadcast({
           "type": "system_card",
           "conversation_id": conversation_id,
           "title": "Low Device Memory",
           "markdown": "Available RAM is below 750 MB. Concurrent session throttled to prevent app closure.",
           "level": "warning"
       })
       return
   ```
3. **SQLite WAL Mode Initialization (`ARGON-V14-12`)**:
   ```python
   # On daemon startup in AntigravityStorageEngine:
   if SUMMARIES_DB.exists():
       conn = sqlite3.connect(str(SUMMARIES_DB), timeout=15.0)
       conn.execute("PRAGMA journal_mode=WAL;")
       conn.execute("PRAGMA busy_timeout=10000;")
       conn.execute("PRAGMA synchronous=NORMAL;")
       conn.close()
   ```
