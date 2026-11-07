# Argon Architectural Audit Report: Antigravity Studio v1.9.0

- **Target**: `specs/v1.9.0/` (Security Hardening, Proxy Patterns, Device APIs & Notification Specifications)
- **Auditor**: `argon-agent (high-reasoning)`
- **Date**: 2026-11-07
- **Overall Status**: **AUDITED & HARDENED** (Passed with 7 Proactive Mitigations)

---

## 1. Executive Summary

The **Argon Architectural Authority** has completed an exhaustive security, threat-modeling, and structural audit of the **Antigravity Studio v1.9.0 specifications**.

The proposed v1.9.0 architecture establishes strict security perimeters across all execution surfaces:
1. It eliminates the ability of autonomous agents or untrusted scripts to bypass disabled MCP servers by introducing an authenticated loopback proxy gatekeeper.
2. It migrates plaintext credentials from disk into hardware-backed Android Keystore storage (AES-256-GCM) with virtual provider routing, while maintaining 100% backward/forward compatibility with password-encrypted `.agy.md` backup bundles.
3. It provisions an in-process Native Device API Bridge gated by a Master Toggle in a new General Settings menu, eliminating fragile third-party dependencies like Termux.
4. It resolves the Android status bar alpha mask rendering constraints by introducing a dedicated monochrome Vector Drawable silhouette (`ic_stat_antigravity.xml`).

All seven proactive hardening mitigations identified below have been incorporated into the specification modules.

---

## 2. Threat Modeling & Hardening Analysis

### Issue ARGON-V19-01: MCP Upstream SSRF & Loopback Target Recursion (CRITICAL)
* **Threat Subsystem**: Loopback Proxy Gatekeeper (`handle_mcp_proxy`).
* **Root Cause**: If an imported or user-supplied MCP configuration defines an `upstreamUrl` resolving to `http://127.0.0.1:8081/api/...` or internal daemon ports, an autonomous agent could exploit the proxy to perform Server-Side Request Forgery (SSRF) against internal app endpoints or bypass auth tokens.
* **Audit Verdict**: **Hardened in Spec 01**.
* **Enforced Mitigation**: The proxy handler strictly inspects the parsed hostname of `upstreamUrl`. Any target resolving to `127.0.0.1`, `localhost`, `::1`, or the active backend port is rejected with HTTP `400 Bad Request` before network dispatch.

### Issue ARGON-V19-02: Proxy Token Verification & Host Port Hijacking Protection (HIGH)
* **Threat Subsystem**: Internal Gateway Security.
* **Root Cause**: While `server.py` binds to loopback (`127.0.0.1`), other processes running under the same UID (or local network probing) could theoretically send forged requests.
* **Audit Verdict**: **Hardened in Spec 01 & Spec 02**.
* **Enforced Mitigation**: Outbound proxy requests must carry an ephemeral session token (`AGY_PROXY_TOKEN`), validated by the proxy middleware to guarantee that requests originate exclusively from the authorized agent or UI child process.

### Issue ARGON-V19-03: StrongBox TEE Hardware Exception & Fallback (HIGH)
* **Threat Subsystem**: Android Keystore Keymaster Provider (`androidx.security.crypto`).
* **Root Cause**: Invoking `.setRequestStrongBoxed(true)` on Android devices lacking a dedicated hardware StrongBox chip (e.g. devices equipped only with standard ARM TrustZone TEE) throws `StrongBoxUnavailableException` or `ProviderException`, causing app initialization failure.
* **Audit Verdict**: **Hardened in Spec 02**.
* **Enforced Mitigation**: MasterKey generation must wrap `setRequestStrongBoxed(true)` in a `try/catch` block, immediately falling back to standard hardware TEE (`setRequestStrongBoxed(false)`).

### Issue ARGON-V19-04: Atomic Migration & Cryptographic Wipe of Plaintext Files (MEDIUM)
* **Threat Subsystem**: Credential Upgrade Routine (`api_keys.json` Migration).
* **Root Cause**: If the process crashes during initial v1.8.0 → v1.9.0 migration after writing to the Keystore vault, an unencrypted `api_keys.json` might linger on disk.
* **Audit Verdict**: **Hardened in Spec 02**.
* **Enforced Mitigation**: Plaintext file removal must be executed atomically: read keys into RAM -> encrypt into Keystore DB -> verify read-back -> overwrite `api_keys.json` with zero-bytes -> unlink.

### Issue ARGON-V19-05: Android 10+ Window Focus Enforcement on Clipboard (MEDIUM)
* **Threat Subsystem**: Device API Bridge (`clipboard_get`).
* **Root Cause**: Starting in Android 10 (API 29), calling `ClipboardManager.getPrimaryClip()` while the application is in the background is blocked by the Android OS and returns empty or throws a SecurityException.
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: `device_api.sock` checks if `MainActivity` has active window focus before reading the clipboard. If backgrounded, it returns error code `CLIPBOARD_UNFOCUSED` with a clear explanation, preventing silent hangs.

### Issue ARGON-V19-06: Strict Internal Storage VFS Isolation for `device_api.sock` (HIGH)
* **Threat Subsystem**: IPC Socket Binding (`device_api.sock`).
* **Root Cause**: Android shared storage (`/storage/emulated/0/...`) is backed by Android FUSE/sdcardfs, which does not support POSIX `AF_UNIX` sockets or discretionary access control (`chmod 0600`).
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: The socket is pinned strictly to internal ext4 storage (`/data/data/ai.agy.app/files/home/.gemini/antigravity-cli/device_api.sock`) with mode `0600` enforced at file creation.

### Issue ARGON-V19-07: Android 12+ (API 31+) PendingIntent Immutability Requirement (HIGH)
* **Threat Subsystem**: Foreground Notification Builder (`AgyForegroundService.kt`).
* **Root Cause**: Android 12+ mandates explicit specification of `PendingIntent.FLAG_IMMUTABLE` or `PendingIntent.FLAG_MUTABLE`. Omitting this causes an `IllegalArgumentException` crash upon notification dispatch.
* **Audit Verdict**: **Hardened in Spec 04**.
* **Enforced Mitigation**: `AgyForegroundService` explicitly sets `PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE`.

---

## 3. Structural & Architectural Invariants Matrix

| Architectural Invariant | Verification Gate | Status | Notes |
| :--- | :--- | :--- | :--- |
| **ARGON-01 (Syscall Traps vs Shims)** | CLI invocation pattern | **PASS** | Scripts invoked explicitly via `python3` / `bash`; no relative `./script` patterns. |
| **ARGON-05 (PendingIntent Security)** | `PendingIntent` creation in `AgyForegroundService` | **PASS** | `FLAG_IMMUTABLE` explicitly specified on all notification intents. |
| **ARGON-06 (VFS Directory Isolation)** | Internal vs Shared Storage | **PASS** | Sockets and keystore DB reside strictly in internal ext4 storage (`0600` mode). |
| **ARGON-07 (WebView Bridge Boundaries)** | In-memory clipboard copy | **PASS** | Routes through `@JavascriptInterface` bridge (`AndroidApp.setClipboardText`). |
| **ARGON-V163 (Backup Cryptography)** | Scrypt KDF + AES-256-GCM | **PASS** | 100% backward and forward compatibility with `.agy.md` backup envelope preserved. |

---

## 4. Implementation Acceptance Criteria

- [ ] **AC-Argon-19.1**: MCP proxy rejects loopback targets (`127.0.0.1`, `localhost`) with HTTP `400 Bad Request` to prevent SSRF.
- [ ] **AC-Argon-19.2**: MasterKey initialization includes a fallback catch to standard TEE if StrongBox is unavailable.
- [ ] **AC-Argon-19.3**: `~/.gemini/config/api_keys.json` is wiped upon first boot after migration to Keystore.
- [ ] **AC-Argon-19.4**: `device_api.sock` is created strictly in `~/.gemini/antigravity-cli/` with mode `0600`.
- [ ] **AC-Argon-19.5**: `clipboard_get` returns `CLIPBOARD_UNFOCUSED` when requested without active window focus.
- [ ] **AC-Argon-19.6**: Notification builder specifies `PendingIntent.FLAG_IMMUTABLE`.
- [ ] **AC-Argon-19.7**: All specs pass automated scan (`python3 ~/.gemini/config/skills/argon-agent/scripts/call_argon.py`) with 0 errors.

---

### Audit Sign-Off
**Architectural Approval**: **GRANTED (HARDENED)**  
*Argon Agent — System Architecture & Security Authority for Antigravity Studio*
