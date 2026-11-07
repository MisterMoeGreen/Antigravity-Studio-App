# Architectural Review & Hardening Matrix

This reference outlines the architectural threat models, system invariants, and hardening mitigations validated across AgyStudio specifications.

---

## 1. System Invariants & Threat Models

### SYS-01: Direct Kernel Syscall Traps vs Linker Shims
- **Threat**: Go and Rust binaries on `linux/arm64` often issue direct kernel `svc #0` traps (e.g. `unlinkat`, `renameat2`) rather than calling libc wrappers. This bypasses LD_PRELOAD dynamic linker shims (`agy_shim.so`).
- **Hazard**: A self-updating binary or malicious script can delete or overwrite critical binaries in `usr/bin/`.
- **Mitigation**: Lock `/files/usr/bin` to mode `0500` (`dr-x------`). Because Android applications run without `CAP_DAC_OVERRIDE`, the Linux VFS kernel blocks all unlink and rename operations with `EACCES`. User-installed scripts reside in `~/.local/bin` (`0700`).

### SYS-02: Ephemeral CI Keystore vs Deterministic Signing
- **Threat**: Running `signingConfigs.getByName("debug")` on fresh GitHub Actions or cloud CI runners generates an ephemeral, random RSA keypair every build.
- **Hazard**: Updating an installed APK with an APK signed by a different key fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, breaking OTA updates and forcing full reinstallation (wiping user workspaces and chat history).
- **Mitigation**: Export developer keystore to `AGY_KEYSTORE_BASE64` secret and load deterministically in Gradle.

### SYS-03: `nativeLibraryDir` Path Randomization
- **Threat**: On every package update or reinstall, Android generates a new randomized hash directory for `nativeLibraryDir` (e.g. `/data/app/~~<hash>/.../lib/arm64`).
- **Hazard**: All symlinks pointing to native binaries or shared objects become dangling pointers after an update.
- **Mitigation**: On service boot or receiving `MY_PACKAGE_REPLACED`, unlock `usr/bin` (`0700`), unconditionally `Os.unlink()` and `Os.symlink()` to the new `nativeLibraryDir`, then re-lock `usr/bin` (`0500`).

### SYS-04: Process Environment vs DEX BuildConfig Boundary
- **Threat**: Background daemon processes (`server.py`, CLI binaries) cannot directly read Android DEX `BuildConfig.APP_VERSION_CODE`.
- **Hazard**: The server and client desynchronize on version checks, leading to false update prompts or version update loops.
- **Mitigation**: `AgyForegroundService` injects `APP_VERSION_CODE` and `APP_VERSION_NAME` into the daemon's process environment at startup. Release automation attaches `release-metadata.json` alongside APK release assets.

### SYS-05: Android 14/15 Intent Mutability & Linker Shim Coverage
- **Threat**: Android 14/15 (`targetSdk 34+`) strictly requires explicit component Intents when creating `FLAG_MUTABLE` PendingIntents. Additionally, path shims missing `renameat2` allow file replacement.
- **Hazard**: App crashes with `IllegalArgumentException` on notification clicks; path traversal bypasses.
- **Mitigation**: Use explicit component intents and `FLAG_IMMUTABLE` where possible. In C shims, hook `renameat2` and verify canonical paths.

### SYS-06: 16KB Page Size Alignment (Android 15)
- **Threat**: Running `patchelf` on `x86_64` host runners defaults to 4KB page alignment.
- **Hazard**: Executables and shared libraries fail to map and crash with segmentation faults on Android 15 devices with 16KB memory page sizes.
- **Mitigation**: Always pass `--page-size 16384` to `patchelf` in cloud builder and local packaging scripts.

### SYS-07: Android WebView Native Bridge Bypass
- **Threat**: Web APIs (`window.open`, `navigator.clipboard.readText`) fail silently or throw SecurityError inside Android WebView.
- **Hazard**: Buttons appear unclickable or fail silently on mobile devices.
- **Mitigation**: Always route system-level actions through `@JavascriptInterface` bridges in native Kotlin (`MainActivity.kt`) running on `MainLooper`.

### SYS-08: Monolithic Python File Class Fragmentation
- **Threat**: Unindented functions in large monolith files (`server.py`) sever Python class scope without syntax errors.
- **Hazard**: Methods become orphaned module-level functions, causing runtime `AttributeError`.
- **Mitigation**: Run `scripts/verify_architectural_invariants.py` in Gradle `preBuild` to assert AST structure before compilation.

### SYS-09: Mobile Touch Target & Stacking Context Collisions
- **Threat**: Buttons inside focused inputs or modals without explicit `z-index` get swallowed by Android hardware-accelerated text selection layers.
- **Hazard**: Buttons are physically unclickable on touchscreen devices.
- **Mitigation**: Enforce minimum 44px touch targets, `z-index: 10+`, `touch-action: manipulation`, and dual `click` + `touchend` event handlers.

---

## 2. Android Storage & Sandbox Invariants

### SELinux W^X Restrictions
- Android enforces W^X (Write XOR Execute). Files on internal user storage (`~`, `/data/data/...`) or external shared storage (`/storage/emulated/0/...`) cannot be executed directly via `./script` even if permissions are `0755`.
- **Enforcement Rule**: Always invoke scripts explicitly via their interpreter (`bash script.sh`, `python3 script.py`).

### Shared Storage (FUSE) Permission Limitations
- `/storage/emulated/0/` is backed by Android FUSE/sdcardfs and does not support POSIX permission bits (`chmod 0600` is a no-op).
- **Enforcement Rule**: Keep sensitive credentials, SSH private keys, and UNIX domain sockets inside internal storage (`~/.ssh`, `~/.gemini/config/`), never on shared storage.
