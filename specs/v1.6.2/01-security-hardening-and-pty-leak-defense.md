# Specification 01: PTY Descriptor Leak Defense, Credential Redaction & Process Hardening

> **Target Release**: `v1.6.2`  
> **Subsystem**: Backend Server & Process Lifecycle (`agy-ui/server.py`)  
> **Component**: Standalone PTY Authentication Engine & Security Hardening  
> **Status**: **IMPLEMENTED & AUDITED BY ARGON**

---

## 1. Problem Statement & Motivation

During the Argon security audit of the `v1.6.1` authentication architecture, three subtle but critical process lifecycle and credential safety vulnerabilities were identified:

1. **PTY Descriptor Leak Hazard (`ARGON-V162-01`)**:
   In `trigger_in_app_login()`, `master, slave = pty.openpty()` opened two file descriptors. However, `self.auth_master = master` was only assigned after the onboarding handshake loop completed. If `asyncio.create_subprocess_exec` failed (e.g. binary permission error) or an exception occurred during the handshake, `slave` was never closed, and `self._cleanup_auth()` could not close `master`. Over time, repeated failed logins would exhaust system file descriptors (`EMFILE`).

2. **Plaintext Authorization Code Exposure in Logs (`ARGON-V162-02`)**:
   When the user submits an authorization code via `submit_auth_code()`, the pseudo-terminal raw echo copies the input into `output_tail`. On verification failure, `server.py` logged `output_tail[-300:]` directly to standard error, exposing sensitive Google OAuth authorization codes in system log streams.

3. **Orphaned Subprocess Escalation Failure (`ARGON-V162-03`)**:
   `_cleanup_auth()` called `self.auth_proc.terminate()` (sending `SIGTERM`). If the child CLI process entered an uninterruptible sleep state or blocked on terminal I/O, it would not terminate, leaving an orphaned CLI process in memory.

---

## 2. Technical Implementation Specifications

### 2.1 PTY Descriptor Lifecycle Hardening
```python
master, slave = pty.openpty()
self.auth_master = master  # Immediate assignment guarantees _cleanup_auth() can close master

try:
    winsize = struct.pack("HHHH", 100, 1000, 0, 0)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)
    fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
except Exception:
    pass

env = dict(os.environ)
env["TERM"] = "xterm-256color"

try:
    proc = await asyncio.create_subprocess_exec(
        os.environ.get("AGY_BIN", str(AGY_BIN)),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
finally:
    # Always close slave in parent process immediately after spawn
    try:
        os.close(slave)
    except Exception:
        pass
```

### 2.2 Credential Redaction in Log Streams
```python
def redact_sensitive_auth_output(text: str) -> str:
    """Redacts OAuth authorization codes and tokens from PTY diagnostic output."""
    if not text:
        return ""
    # Redact Google OAuth auth codes: 4/0A...
    redacted = re.sub(r"4/[a-zA-Z0-9_\-]{15,}", "4/[REDACTED_AUTH_CODE]", text)
    # Redact Google OAuth access tokens: ya29...
    redacted = re.sub(r"ya29\.[a-zA-Z0-9_\-]+", "ya29.[REDACTED_ACCESS_TOKEN]", redacted)
    # Redact Google OAuth refresh tokens: 1//...
    redacted = re.sub(r"1//[a-zA-Z0-9_\-]+", "1//[REDACTED_REFRESH_TOKEN]", redacted)
    # Redact URL query parameters
    redacted = re.sub(r"([?&](?:code|token|key|secret)=)[^&\s]+", r"\1[REDACTED]", redacted)
    return redacted
```

### 2.3 Asynchronous Process Escalation & Zombie Reaping
```python
proc_to_kill = self.auth_proc
self.auth_proc = None
if proc_to_kill:
    async def _reap_proc(p):
        try:
            if p.returncode is None:
                p.terminate()
                try:
                    await asyncio.wait_for(p.wait(), timeout=0.5)
                except (asyncio.TimeoutError, Exception):
                    if p.returncode is None:
                        p.kill()
                        try:
                            await asyncio.wait_for(p.wait(), timeout=0.5)
                        except Exception:
                            pass
        except Exception:
            pass

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_reap_proc(proc_to_kill))
    except RuntimeError:
        try:
            if proc_to_kill.returncode is None:
                proc_to_kill.terminate()
                proc_to_kill.kill()
        except Exception:
            pass
```

---

## 3. Acceptance Criteria (AC)

- [x] **AC-1.6.2.1**: `pty.openpty()` immediately sets `self.auth_master = master` so any subsequent exception triggers descriptor closure via `_cleanup_auth()`.
- [x] **AC-1.6.2.2**: `slave` is closed in a `finally:` block, preventing file descriptor leaks if subprocess execution fails.
- [x] **AC-1.6.2.3**: `logger.error`, `logger.warning`, and error exceptions redact all OAuth authorization codes, access tokens (`ya29...`), and refresh tokens (`1//...`).
- [x] **AC-1.6.2.4**: Subprocess cleanup escalates from `SIGTERM` to `SIGKILL` asynchronously and awaits `wait()` to cleanly reap child processes.
- [x] **AC-1.6.2.5**: Release metadata, `version.properties`, and runtime headers are synchronized to `v1.6.2` (build `47`).
- [x] **AC-1.6.2.6**: `AndroidApp.openUrl(url)` JavaScript bridge and HTML5 anchor fallback ensure OAuth URLs open in Chrome Custom Tabs from Android WebView.
- [x] **AC-1.6.2.7**: Class scope integrity verified so `trigger_in_app_login` and `submit_auth_code` remain methods of `AgyProcessOrchestrator`.
- [x] **AC-1.6.2.8**: Bounded WebSocket message queue (`MAX_WS_QUEUE = 50`) prevents memory growth during disconnected states.
- [x] **AC-1.6.2.9**: Touch close button on auth overlay and Android back gesture dismiss `#loginOverlay` when the session is already authenticated.
- [x] **AC-1.6.2.10**: Native Android `getClipboardText()` bridge on `AndroidAppInterface` and enhanced touch targets (`z-index: 10`, `touchend` dispatch) ensure reliable one-tap code pasting from clipboard.
