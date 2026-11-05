# Argon Architectural Audit Report: Antigravity Studio v1.8.0

- **Target**: `specs/1.8.0/` (ACP Architecture & Migration Specifications)
- **Auditor**: `argon-agent (high-reasoning)`
- **Date**: 2026-11-05
- **Overall Status**: **AUDITED & HARDENED** (Passed with 4 Proactive Mitigations)

---

## 1. Executive Summary

The **Argon Architectural Authority** has performed a deep-reasoning security audit and structural analysis of the proposed **v1.8.0 ACP Migration Specification**. 

The transition from batch one-shot CLI execution (`agy -p`) to a persistent Agent Client Protocol daemon (`agy acp`) represents a major architectural advancement: it eliminates turn cold-start overhead, stabilizes event streaming on typed JSON-RPC 2.0, preserves in-memory token cache reuse across turns, and eliminates destructive process kills.

All core Android sandbox boundaries (SELinux W^X policies, ext4 vs FUSE storage isolation, and Bionic/glibc dynamic linker boundaries) have been verified. The 4 proactive hardening mitigations identified below ensure flawless production stability under Android platform constraints.

---

## 2. Threat Modeling & Hardening Analysis

### Issue ARGON-V18-01: Anonymous ELF Sub-Worker Pipe Deadlock (CRITICAL)
* **Threat Subsystem**: Daemon Concurrency Model (`--process-per-session`).
* **Root Cause**: On Android, native ARM64 binaries operate through the W^X-compliant anonymous-memory ELF loader. When the upstream `--process-per-session` flag is passed, `agy acp` attempts to re-exec sub-worker processes connected via internal anonymous pipes. This triggers `write |1: broken pipe` and crashes session creation.
* **Audit Verdict**: **Hardened in Spec 01**.
* **Enforced Mitigation**: The spec strictly mandates **single-process stdio multiplexing**. Multiple sessions (`s1`, `s2`, ...) run concurrently within a single daemon instance and are safely demultiplexed via the top-level `sessionId` property present on all ACP requests and notifications.

### Issue ARGON-V18-02: Headless Display Absence during OAuth Handshake (HIGH)
* **Threat Subsystem**: Authentication Subsystem (`session/authenticate`).
* **Root Cause**: In standard desktop environments, calling `authenticate` with `methodId: "oauth-personal"` triggers an OS browser launcher (`xdg-open`). Inside the Android Termux headless subsystem, this call throws JSON-RPC error `-32603` (`"No browser is available here..."`).
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: The architecture enforces an exclusive **Google OAuth PTY Gate**:
  * Google OAuth uses the proven PTY runner executing `agy acp --login` (or `agy --login`) to capture the OAuth consent URL, routing it to native Android Chrome Custom Tabs via `@JavascriptInterface` (`MainActivity.kt`). API key authentication is not supported.

### Issue ARGON-V18-03: Stdio Deserialization Resilience on Interleaved stderr (MEDIUM)
* **Threat Subsystem**: Event Stream Reader (`_consume_stdout_stream`).
* **Root Cause**: While ACP JSON-RPC lines are emitted on stdout, unexpected runtime diagnostics, glibc linker notices, or plugin warnings can occasionally leak into stdout or arrive on stderr simultaneously. A naive `json.loads(line)` parser will crash the persistent reader loop if an unformatted line is encountered.
* **Audit Verdict**: **Proactive Mitigation Added**.
* **Enforced Mitigation**: `_consume_stdout_stream` must wrap all deserialization in a resilient `try/except json.JSONDecodeError` block. Malformed non-JSON lines must be diverted to internal diagnostic logs without terminating the daemon connection.

### Issue ARGON-V18-04: Daemon Re-Authentication upon Session Expiry (MEDIUM)
* **Threat Subsystem**: Token Refresh & Long-Lived Daemon State.
* **Root Cause**: In batch mode (`agy -p`), expired Google tokens triggered an immediate re-auth prompt on the subsequent turn. In a persistent `agy acp` daemon running continuously across days, a session may receive an auth error (`stopReason: "auth_required"` or `401 Unauthorized`) mid-conversation.
* **Audit Verdict**: **Proactive Mitigation Added**.
* **Enforced Mitigation**: When an in-flight `session/prompt` returns an authentication failure, `AgyProcessOrchestrator` must emit `auth_required` over WebSocket, initiate the PTY login runner, and upon `_on_auth_success`, re-issue `session/load` to seamlessly resume the active session without loss of history.

---

## 3. Structural & Architectural Invariants Matrix

| Architectural Invariant | Verification Gate | Status | Notes |
| :--- | :--- | :--- | :--- |
| **ARGON-01 (Syscall Traps vs Shims)** | Directory mode `0500` on `usr/bin/` | **PASS** | Symlink `/usr/bin/agy` is write-protected; launcher binary is immutable in APK `nativeLibraryDir`. |
| **ARGON-07 (WebView Bridge Boundaries)** | `openUrl`, `getClipboardText` in Kotlin | **PASS** | OAuth consent URL launching routes through native Chrome Custom Tabs. |
| **ARGON-08 (AST Scope Integrity)** | `scripts/verify_architectural_invariants.py` | **PASS** | All 11 mandatory methods of `AgyProcessOrchestrator` retained in class body. |
| **ARGON-09 (Touch Target Standards)** | Mobile input & Stop button | **PASS** | Classic Stop-First mode (Option 2) preserves 44px min touch target and eliminates touch-selection conflicts. |

---

## 4. Implementation Acceptance Criteria

- [ ] **AC-Argon-18.1**: Daemon launcher passes `--dangerously-skip-permissions` and **never** passes `--process-per-session`.
- [ ] **AC-Argon-18.2**: `_consume_stdout_stream` gracefully skips non-JSON diagnostic lines with debug-level logging and never aborts the event loop on parse errors.
- [ ] **AC-Argon-18.3**: `session/cancel` is dispatched as a one-way notification without an `id` field when the user taps "Stop".
- [ ] **AC-Argon-18.4**: Interactive bottom-sheets continue to route through `ask_question.sock` with zero regressions in option rendering.
- [ ] **AC-Argon-18.5**: `python3 scripts/verify_architectural_invariants.py` completes with exit code `0` before staging any release build.

---

### Audit Sign-Off
**Architectural Approval**: **GRANTED**  
The v1.8.0 specification is robust, fully hardened against Android environment edge cases, and ready for code implementation.
