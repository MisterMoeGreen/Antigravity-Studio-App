# Architectural Audit & Security Report: Antigravity Studio v1.9.0

- **Target**: `specs/v1.9.0/` (Security Hardening, Proxy Patterns, Device APIs & Notification Specifications)
- **External Reviewer**: `Gemini 4 Argon (gemini-4-argon)` via Google GenAI API
- **Internal Auditor**: `review-agent (automated)`
- **Date**: 2026-11-07
- **Overall Status**: **AUDITED, HARDENED & APPROVED** (All 10 Argon Refinements Incorporated)

---

## 1. Executive Summary

The **Antigravity Studio v1.9.0** specifications underwent an independent, high-reasoning systems and threat-modeling review conducted by **Gemini 4 Argon** (`gemini-4-argon`). 

Argon issued a **Conditional Approval requiring 10 targeted hardening refinements** across the Linux/Android UID boundary, the HTTP/SSE proxy layer, provider SDK authentication protocols, and the Android UI framework.

All 10 refinements have been designed, validated, and directly incorporated into the v1.9.0 specification modules:
1. **SSRF Open-Redirect Gate (`ARCH-V19-01`)**: Set `allow_redirects=False` and enforced `socket.getaddrinfo` + `ipaddress` validation to block redirect SSRF and IP encoding tricks (`127.0.0.2`, `2130706433`, `[::ffff:127.0.0.1]`).
2. **SSE Streaming & Subpath Support**: Extended `/mcp-proxy/{name}` to route subpaths and query strings, with `ClientTimeout(total=None, sock_read=300.0)` to eliminate 60-second connection drops.
3. **Stdio Definition Preservation**: Preserved complete stdio configurations (`command`, `args`, `env`) inside the encrypted vault before neutralizing workspace configs.
4. **Same-UID Memory Protection**: Added `prctl(PR_SET_DUMPABLE, 0)` on `server.py` boot to prevent same-UID child processes from inspecting `/proc/<server_pid>/mem`.
5. **Multi-Provider Header Injection Table**: Implemented provider-specific authentication handling for Anthropic (`x-api-key`), Gemini (`x-goog-api-key`), OpenAI (`Authorization: Bearer`), and Tavily.
6. **Android 13+ Sensitive Clipboard Masking**: Added `ClipDescription.EXTRA_IS_SENSITIVE = true` to hide plaintext keys in Android 13+ screen toast overlays.
7. **Host-Authoritative Settings Gate (`ARCH-V19-08`)**: Restricted Device API Master Switch and sub-toggles to host Android `SharedPreferences`, completely removing `~/.gemini/app_settings.json` from the authorization decision path.
8. **Scheme & Path Traversal Guards (`ARCH-V19-09`, `ARCH-V19-10`)**: Restricted `open_url` to `http/https` and enforced canonical workspace path containment on `take_photo`.
9. **Camera Foreground Lifecycle State**: Enforced active `MainActivity` foreground state (`RESUMED`) before opening Camera2 on Android 14+ (targetSdk 35).
10. **Adaptive Icon Canvas Rendering (`ARCH-V19-11`)**: Replaced `BitmapFactory.decodeResource()` with a `Canvas` draw helper to eliminate `null` returns for `AdaptiveIconDrawable` instances on API 26+.

---

## 2. Threat Modeling & Hardening Analysis

### Issue ARCH-V19-01: MCP Upstream SSRF, Open Redirects & Loopback Encoding (CRITICAL)
* **Threat Subsystem**: Loopback Proxy Gatekeeper (`handle_mcp_proxy`).
* **Root Cause**: Passing `allow_redirects=True` allowed an external MCP server to issue an HTTP `302 Found` redirecting back to `http://127.0.0.1:8081/api/...`, bypassing hostname checks. Furthermore, non-canonical representations (`127.0.0.2`, `2130706433`, `[::ffff:127.0.0.1]`) bypass simple string matching.
* **Audit Verdict**: **Hardened in Spec 01**.
* **Enforced Mitigation**: Set `allow_redirects=False`. Resolved all DNS targets using `socket.getaddrinfo()` and validated every resolved IP using Python's `ipaddress` module (`is_loopback`, `is_unspecified`, `is_link_local`, `is_multicast`, `ipv4_mapped`).

### Issue ARCH-V19-02: Proxy Token Verification & Host Port Hijacking Protection (HIGH)
* **Threat Subsystem**: Internal Gateway Security.
* **Root Cause**: While `server.py` binds to loopback (`127.0.0.1`), other processes running under the same UID could send forged requests.
* **Audit Verdict**: **Hardened in Spec 01 & Spec 02**.
* **Enforced Mitigation**: Outbound proxy requests must carry an ephemeral session token (`AGY_PROXY_TOKEN`), validated by the proxy middleware.

### Issue ARCH-V19-03: StrongBox TEE Hardware Exception & Fallback (HIGH)
* **Threat Subsystem**: Android Keystore Keymaster Provider (`androidx.security.crypto`).
* **Root Cause**: Invoking `.setRequestStrongBoxed(true)` on Android devices lacking a dedicated hardware StrongBox chip throws `StrongBoxUnavailableException` or `ProviderException`.
* **Audit Verdict**: **Hardened in Spec 02**.
* **Enforced Mitigation**: Wrapped `setRequestStrongBoxed(true)` in a `try/catch` block, immediately falling back to standard hardware TEE (`setRequestStrongBoxed(false)`).

### Issue ARCH-V19-04: Atomic Migration & Cryptographic Wipe of Plaintext Files (MEDIUM)
* **Threat Subsystem**: Credential Upgrade Routine (`api_keys.json` Migration).
* **Root Cause**: If the process crashes during initial v1.8.0 → v1.9.0 migration after writing to the Keystore vault, an unencrypted `api_keys.json` might linger on disk.
* **Audit Verdict**: **Hardened in Spec 02**.
* **Enforced Mitigation**: Executed atomic migration: read keys -> encrypt into Keystore DB -> verify read-back -> overwrite `api_keys.json` with zero-bytes -> unlink.

### Issue ARCH-V19-05: Android 10+ Window Focus Enforcement on Clipboard (MEDIUM)
* **Threat Subsystem**: Device API Bridge (`clipboard_get`).
* **Root Cause**: Starting in Android 10 (API 29), calling `ClipboardManager.getPrimaryClip()` while the application is in the background is blocked by the OS.
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: `device_api.sock` checks if `MainActivity` has active window focus before reading the clipboard. If backgrounded, it returns error code `CLIPBOARD_UNFOCUSED`.

### Issue ARCH-V19-06: Strict Internal Storage VFS Isolation for `device_api.sock` (HIGH)
* **Threat Subsystem**: IPC Socket Binding (`device_api.sock`).
* **Root Cause**: Android shared storage (`/storage/emulated/0/...`) is backed by Android FUSE, which does not support POSIX `AF_UNIX` sockets or discretionary access control (`chmod 0600`).
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: Socket is pinned strictly to internal ext4 storage (`~/.gemini/antigravity-cli/device_api.sock`) with mode `0600` enforced at file creation.

### Issue ARCH-V19-07: Android 12+ (API 31+) PendingIntent Immutability Requirement (HIGH)
* **Threat Subsystem**: Foreground Notification Builder (`AgyForegroundService.kt`).
* **Root Cause**: Android 12+ mandates explicit specification of `PendingIntent.FLAG_IMMUTABLE`. Omitting this causes an `IllegalArgumentException` crash upon notification dispatch.
* **Audit Verdict**: **Hardened in Spec 04**.
* **Enforced Mitigation**: Explicitly set `PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE`.

### Issue ARCH-V19-08: Same-UID Privilege Escalation on Device API Toggles (CRITICAL)
* **Threat Subsystem**: General Settings Hub & Device API Permission Gatekeeper.
* **Root Cause**: Storing Device API toggles in `~/.gemini/app_settings.json` allowed an autonomous agent with local file write access to edit the file and enable the Master Switch itself.
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: Device API Master Switch and sub-toggles are stored exclusively in host Android `SharedPreferences`. Mutated only via native `@JavascriptInterface` calls from touch events in `MainActivity.kt`.

### Issue ARCH-V19-09: `open_url` Scheme Injection & `take_photo` Path Traversal (HIGH)
* **Threat Subsystem**: Native Device API Actions.
* **Root Cause**: Unvalidated URIs allowed `file://` or `intent://` schemes to hijack internal components, while unconstrained `dest_path` allowed writing photos to arbitrary directories.
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: Enforced `http/https` only on `open_url`, and verified `canonicalFile` containment within the workspace directory on `take_photo`.

### Issue ARCH-V19-10: Android 14/15 Background Camera Restrictions (HIGH)
* **Threat Subsystem**: Camera Hardware Access (`take_photo`).
* **Root Cause**: On Android 9–14+ (targetSdk 35), camera hardware access from a backgrounded UID triggers a `SecurityException`.
* **Audit Verdict**: **Hardened in Spec 03**.
* **Enforced Mitigation**: `device_api.sock` verifies that `MainActivity` is in the foreground (`Lifecycle.State.RESUMED`) before opening Camera2; if backgrounded, it returns `CAMERA_REQUIRES_FOREGROUND`.

### Issue ARCH-V19-11: AdaptiveIconDrawable Null Crash in `setLargeIcon` (HIGH)
* **Threat Subsystem**: Notification Large Icon Builder (`AgyForegroundService.kt`).
* **Root Cause**: On Android 8.0+ (API 26+), `R.mipmap.ic_launcher` is an XML `AdaptiveIconDrawable`. `BitmapFactory.decodeResource()` returns `null` for XML drawables.
* **Audit Verdict**: **Hardened in Spec 04**.
* **Enforced Mitigation**: Implemented `getLauncherLargeIconBitmap()` using a `Canvas` draw helper to safely render `AdaptiveIconDrawable` into a non-null `Bitmap`.

---

## 3. Structural & Architectural Invariants Matrix

| Architectural Invariant | Verification Gate | Status | Notes |
| :--- | :--- | :--- | :--- |
| **SYS-01 (Syscall Traps vs Shims)** | CLI invocation pattern | **PASS** | Scripts invoked explicitly via `python3` / `bash`; no relative `./script` patterns. |
| **SYS-05 (PendingIntent Security)** | `PendingIntent` creation in `AgyForegroundService` | **PASS** | `FLAG_IMMUTABLE` explicitly specified on all notification intents. |
| **SYS-06 (VFS Directory Isolation)** | Internal vs Shared Storage | **PASS** | Sockets and keystore DB reside strictly in internal ext4 storage (`0600` mode). |
| **SYS-07 (WebView Bridge Boundaries)** | In-memory clipboard copy | **PASS** | Routes through `@JavascriptInterface` bridge (`AndroidApp.copyVaultKeyToClipboard`). |
| **SYS-08 (AST Scope Integrity)** | `scripts/verify_architectural_invariants.py` | **PASS** | Class scoping preserved for all orchestrator methods. |
| **SYS-V163 (Backup Cryptography)** | Scrypt KDF + AES-256-GCM | **PASS** | 100% backward and forward compatibility with `.agy.md` backup envelope preserved. |

---

## 4. Implementation Acceptance Criteria

- [ ] **AC-Rev-19.1**: MCP proxy rejects loopback targets (`127.0.0.1`, `localhost`, `[::ffff:127.0.0.1]`, `2130706433`) via `socket.getaddrinfo` + `ipaddress` validation.
- [ ] **AC-Rev-19.2**: MCP proxy sets `allow_redirects=False` to prevent open-redirect SSRF.
- [ ] **AC-Rev-19.3**: MCP proxy uses shared `ClientSession` with streaming-safe timeouts (`total=None, sock_read=300.0`) and preserves subpaths + query strings.
- [ ] **AC-Rev-19.4**: Disabling stdio MCPs preserves complete command, args, and env in `mcp_vault` without parameter loss.
- [ ] **AC-Rev-19.5**: MasterKey initialization includes a fallback catch to standard TEE if StrongBox is unavailable.
- [ ] **AC-Rev-19.6**: `server.py` calls `prctl(PR_SET_DUMPABLE, 0)` on boot, preventing same-UID memory inspection.
- [ ] **AC-Rev-19.7**: API key proxy correctly injects provider-specific authentication (`x-api-key` for Anthropic, `x-goog-api-key` for Gemini, `Authorization: Bearer` for OpenAI, and body injection for Tavily).
- [ ] **AC-Rev-19.8**: Tapping "Copy" on an API key card in Settings marks `ClipDescription.EXTRA_IS_SENSITIVE = true` on Android 13+.
- [ ] **AC-Rev-19.9**: Device API Master Switch and sub-toggles are stored exclusively in host Android `SharedPreferences` and cannot be modified via `app_settings.json`.
- [ ] **AC-Rev-19.10**: `open_url` rejects non-http/https schemes (`file://`, `intent://`), and `take_photo` enforces canonical workspace path containment.
- [ ] **AC-Rev-19.11**: `take_photo` verifies active `MainActivity` foreground lifecycle state (`RESUMED`) on Android 14+ before opening Camera2.
- [ ] **AC-Rev-19.12**: `AgyForegroundService.kt` converts `R.mipmap.ic_launcher` via `Canvas` to guarantee a non-null `Bitmap` for `setLargeIcon`.
- [ ] **AC-Rev-19.13**: All specs pass automated scan (`python3 .agents/skills/review-agent/scripts/call_review.py`) with 0 errors.

---

### Audit Sign-Off
**Architectural Approval**: **GRANTED (HARDENED & AUDITED BY GEMINI 4 ARGON)**  
*Review Agent & Gemini 4 Argon — System Architecture & Security Authority for Antigravity Studio*
