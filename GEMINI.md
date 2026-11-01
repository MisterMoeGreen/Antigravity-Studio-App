# Antigravity Studio — Project Guide & Build Specifications

This document defines the architecture, environment requirements, upstream binary synchronization, and build procedures for **Antigravity Studio** (`ai.agy.app`).

---

## 1. Architecture & Geometry Overview

- **Package ID**: `ai.agy.app`
- **Byte Geometry**: Exactly 10 ASCII characters (`ai.agy.app`), identical in byte-length to `com.termux` (10 chars). This 1:1 length match allows byte-level binary patching across all compiled native binaries, glibc shared objects, and scripts without altering ELF file offsets.
- **Storage Subsystem**:
  - The repository resides on Android shared storage (`/storage/emulated/0/Documents/AgyStudio` or `~/storage/shared/Documents/AgyStudio`), backed by Android FUSE.
  - **CRITICAL (noexec & permissions)**: Android shared storage is mounted `noexec` and does **not** support POSIX `chmod +x` or UNIX permissions (`0600`/`0700`). All scripts and wrappers (including `gradlew` and `scripts/*.sh`) must always be executed explicitly through their interpreter (e.g., `bash ./gradlew`, `bash scripts/verify_or_patch_libagy.sh`).
  - **Git Configuration on FUSE**: Android FUSE forces fixed permission masks. To prevent Git from falsely flagging all files as modified or failing on ownership checks, ensure:
    ```bash
    git config core.fileMode false
    git config --global --add safe.directory /storage/emulated/0/Documents/AgyStudio
    ```
  - **Temporary Files & Sockets**: FUSE blocks `symlink()` and `AF_UNIX` domain socket creation. All compilation staging, virtual environments, and IPC sockets must reside in Termux internal ext4 storage (`$TMPDIR` or `/data/data/com.termux/files/usr/tmp`), never inside the repo tree on shared storage.
  - Private app storage resides inside `/data/data/ai.agy.app/files/` with separate ext4 sandboxed permissions for internal tokens and sockets.

---

## 2. Preconditions & Host Packages (Termux Environment)

To build Antigravity Studio locally on an Android device running Termux, the following packages and configurations are required:

### 2.1 Required Termux Packages
Ensure the following tools are installed:
```bash
pkg install -y \
  openjdk-17 \
  aapt2 \
  patchelf \
  llvm \
  binutils \
  python3 \
  zstd \
  tar \
  bash \
  git \
  apksigner
```

> [!IMPORTANT]
> **Mandatory `patchelf` version**: Version `0.19.2` or later is mandatory. Older versions (such as `0.18.0`) corrupt the `DT_STRTAB` section on 200MB+ Go/ELF binaries when adjusting 16KB page alignment, causing instant `SIGSEGV` (exit code -11).

### 2.2 Android SDK Configuration
Create or verify `local.properties` in the project root:
```properties
sdk.dir=/data/data/com.termux/files/home/android-sdk
```

The Android SDK must contain:
- Platforms: `android-35` (compileSdk 35 / targetSdk 35)
- Build-Tools: `35.0.0` or compatible

### 2.3 AAPT2 & Gradle Properties
Verify `gradle.properties` in the project root contains:
```properties
org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
org.gradle.daemon=false
android.useAndroidX=true
kotlin.code.style=official
android.nonTransitiveRClass=true
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
android.suppressUnsupportedCompileSdk=35
```

> **Why `org.gradle.daemon=false` is required**: In the Android/PRoot container environment, persistent background Gradle daemons keep pipe descriptors open, which causes subshells and terminal sessions to hang indefinitely after builds finish. Disabling the daemon forces Gradle to run in single-use mode and exit immediately upon completion.
>
> **Why `android.aapt2FromMavenOverride` is required**: The Google Maven-distributed `aapt2` x86_64/glibc binary cannot run on native Android ARM64 Termux. Pointing Gradle to the native Termux `aapt2` package ensures fast, native resource compilation.

### 2.4 Signing Keystore
Release APKs are signed with a persistent `debug.keystore`:
- Location: `.android/debug.keystore` (or `~/.android/debug.keystore`)
- Password: `android`
- Key Alias: `androiddebugkey`
- Key Password: `android`

Gradle's build script automatically discovers keystores in `.android/` or `~/.android/`.

---

## 3. Upstream Google Antigravity CLI (`agy`) Binary Management

Antigravity Studio packages the standalone Antigravity CLI binary inside the APK as `lib/arm64-v8a/libagy.so`. At app launch, the bootstrapper symlinks `$usr/bin/agy` to this native library.

### 3.1 Upstream Source
- Termux local source: `/data/data/com.termux/files/usr/bin/agy.va39`
- Upstream GitHub releases: `https://github.com/wallentx/antigravity-cli-termux/releases/latest`

To update Termux's local `agy` to the newest upstream version before building:
```bash
agy update -y
```

### 3.2 Automated Binary Verification & Patching
The project includes an automated integrity checker and patcher at [`scripts/verify_or_patch_libagy.sh`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/scripts/verify_or_patch_libagy.sh). This script:
1. Inspects `app/src/main/jniLibs/arm64-v8a/libagy.so`.
2. Validates ELF header and segment mappings using `llvm-readelf` to enforce the following **4 invariants**:
   - `PT_INTERP` points to `/data/data/ai.agy.app/files/usr/glibc/lib/ld-linux-aarch64.so.1`.
   - `DT_RPATH` / `DT_RUNPATH` points to `/data/data/ai.agy.app/files/usr/glibc/lib`.
   - `PT_LOAD` segments conform to 16KB (`0x4000`) page alignment.
   - `DT_STRTAB` virtual address falls strictly within a valid, memory-mapped `PT_LOAD` segment with zero residual `/data/data/com.termux` strings.
3. If missing or corrupted, copies `/data/data/com.termux/files/usr/bin/agy.va39`, applies 16KB page alignment (`patchelf --page-size 16384`), configures the Glibc dynamic interpreter and RPATH, and applies byte-safe replacement of `/data/data/com.termux` with `/data/data/ai.agy.app`.

This script is registered directly as the Gradle task `verifyLibAgy` and runs automatically before every build (`preBuild`).

To manually run the verification/patch script at any time:
```bash
bash scripts/verify_or_patch_libagy.sh
```

To diagnose or sanity-check the binary headers directly:
```bash
llvm-readelf -l -d app/src/main/jniLibs/arm64-v8a/libagy.so | grep -E "INTERP|RPATH|RUNPATH|STRTAB|LOAD|warning|error"
```

### 3.3 Runtime Execution & Glibc vs. Bionic Isolation
- `libagy.so` is a **Glibc dynamic executable**. Android OS and Termux utilities run under **Bionic** `libc.so`.
- When spawning `agy` from Python (`server.py`) or Kotlin, **never** set `LD_LIBRARY_PATH` to `/data/data/ai.agy.app/files/usr/lib` (Bionic). Let `libagy.so` resolve its dependencies solely through its embedded `DT_RPATH` (`/data/data/ai.agy.app/files/usr/glibc/lib`).
- In `app/build.gradle.kts`, `packaging.jniLibs.useLegacyPackaging = true` **must remain enabled**. This forces physical extraction of native binaries to `/data/app/.../lib/arm64/` with executable permissions, allowing `execve()` on Android.

---

## 4. Step-by-Step Guide: Building the APK

Follow these steps to build and stage an updated release APK:

### Step 1: Ensure Latest `agy` Binary
Ensure Termux has the latest standalone `agy.va39` binary:
```bash
agy update -y
```

Run the binary verification script to synchronize and patch it into `jniLibs`:
```bash
bash scripts/verify_or_patch_libagy.sh
```

### Step 2: Bump Version in `version.properties`
For Android to install an update in-place without requiring the user to uninstall the previous version, `VERSION_CODE` **must be strictly greater** than the currently installed build:

Edit [`version.properties`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/version.properties):
```properties
VERSION_CODE=43
VERSION_NAME=1.6.1
AGY_VERSION=1.4.12
AGY_SHA256=...
```

### Step 3: Run the Gradle Release Build
> [!IMPORTANT]
> **Always invoke via `bash ./gradlew`!**
> Because the repository is located on Android FUSE shared storage, running bare `./gradlew` will fail with `Permission denied`. Always use `bash ./gradlew`. Bound max workers and disable the background daemon to avoid hangs and triggering Android's Phantom Process Killer:

```bash
bash ./gradlew assembleRelease --max-workers=2 --no-daemon
```

During this build, Gradle will automatically:
1. Execute `verifyLibAgy` to validate `libagy.so` ELF integrity.
2. Execute `syncWebAssets` to repackage latest `agy-ui/` files into `usr-data.tar.zst`.
3. Compile Kotlin and Java sources.
4. Package native libraries and assets into `app-release.apk`.
5. Sign the APK using the persistent keystore.

### Step 4: Verify & Stage the Output APK
Verify that the APK generated successfully and that `libagy.so` inside it has valid ELF headers:
```bash
aapt dump badging app/build/outputs/apk/release/app-release.apk | head -n 3
```

Stage the APK to the user's **Downloads** folder for one-tap installation:
```bash
cp -f app/build/outputs/apk/release/app-release.apk /storage/emulated/0/Download/AntigravityStudio.apk
cp -f app/build/outputs/apk/release/app-release.apk /storage/emulated/0/Download/AntigravityStudio-v1.6.1.apk
```

---

## 5. Troubleshooting & Common Pitfalls

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **`./gradlew: Permission denied`** | Shared storage (`/storage/emulated/0`) lacks POSIX execution bits (`noexec`). | Always run through the bash interpreter: `bash ./gradlew <tasks>`. |
| **Git reports every file modified or "dubious ownership"** | Android FUSE assigns fixed permissions and non-Termux UID. | Run `git config core.fileMode false` and `git config --global --add safe.directory /storage/emulated/0/Documents/AgyStudio`. |
| **`exit code -11` (SIGSEGV) in `agy`** | `libagy.so` was patched with buggy patchelf or unaligned pages, causing `unable to parse DT_STRTAB: virtual address is not in any segment`. | Run `bash scripts/verify_or_patch_libagy.sh` to cleanly re-patch from `agy.va39`. |
| **"Google Authentication Required" lock sheet appears even when signed in** | `server.py` runs `agy models` on startup to discover models. If `agy` crashes with code -11, `models` is empty, triggering the auth lock sheet. | Fixing the `libagy.so` segfault resolves this automatically. |
| **AAPT2 daemon crash / `ExecException`** | Google's x86_64 AAPT2 was invoked instead of native ARM64 AAPT2. | Ensure `android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2` is set in `gradle.properties`. |
| **"App not installed as package appears to be invalid"** | `VERSION_CODE` was not incremented, or signature keystore differed from previous installation. | Bump `VERSION_CODE` in `version.properties` and verify `.android/debug.keystore` is used. |
| **Build process killed abruptly / Termux crashes** | Android Phantom Process Killer killed high-concurrency Gradle worker pool. | Pass `--max-workers=2` or `--no-daemon` to `bash ./gradlew`. |
| **`libagy.so` fails with symbol errors on launch** | `LD_LIBRARY_PATH` passed Bionic `/usr/lib` to Glibc `libagy.so`. | Unset `LD_LIBRARY_PATH` or exclude Bionic paths; rely on `libagy.so`'s embedded `DT_RPATH`. |
| **`Permission denied` when creating sockets or symlinks** | Attempted to create UNIX domain sockets or symlinks on FUSE storage. | Use Termux ext4 internal storage (`$TMPDIR` or `/data/data/com.termux/files/usr/tmp`). |

---

## 6. Mandatory Hybrid Runtime & Architectural Invariants

Every developer and AI agent contributing to Antigravity Studio MUST strictly adhere to these 4 non-negotiable architectural rules. These rules are enforced automatically before every compilation via `scripts/verify_architectural_invariants.py`:

1. **The Native-First Hybrid WebView Rule (ARGON-07)**:
   - Android `WebView` is an embedded container with restricted browser APIs.
   - **NEVER** use `window.open()` or `navigator.clipboard.readText()` directly without a native `@JavascriptInterface` bridge.
   - All OS-level actions (clipboard read/write, URL launching, notifications, file picking) must route through native Kotlin methods in `MainActivity.kt` running on Android's `MainLooper`.

2. **Zero-Assumption Binary Fixtures (ARGON-09)**:
   - **NEVER** write a regex or parser against an external CLI binary (`agy`, `git`, `curl`) based on memory or assumptions.
   - Before writing integration code, execute the binary in an isolated scratch script, dump `repr(output)` to a fixture file, and verify the exact byte stream.

3. **Automated AST Class Scope Integrity (ARGON-08)**:
   - When modifying `agy-ui/server.py` (7,700+ lines), all methods of `AgyProcessOrchestrator` must remain strictly indented inside the class body.
   - The pre-build gate `scripts/verify_architectural_invariants.py` automatically parses the AST and aborts the build if any orchestrator method is missing or fragmented.

4. **Mobile Touch Target & Stacking Standards**:
   - Minimum touch target: 44px × 44px (or 48dp).
   - Any interactive button positioned inside input wrappers or overlays must declare explicit `z-index: 10+`, `touch-action: manipulation`, and dual `click` + `touchend` event binding to eliminate the 300ms mobile tap delay and prevent input field focus layers from swallowing touch events.
