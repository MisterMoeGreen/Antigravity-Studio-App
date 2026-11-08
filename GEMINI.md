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
  - **Temporary Files & Sockets**: FUSE blocks `symlink()` and `AF_UNIX` domain socket creation. All compilation staging, virtual environments, and IPC sockets must reside in internal ext4 storage (`$TMPDIR` or `/data/data/ai.agy.app/files/usr/tmp`), never inside the repo tree on shared storage.
  - Private app storage resides inside `/data/data/ai.agy.app/files/` with separate ext4 sandboxed permissions for internal tokens and sockets.

---

## 2. Preconditions & Subsystem Environment (Alpine Linux Subsystem)

To build Antigravity Studio locally on an Android device, builds must be executed inside the integrated Alpine Linux container subsystem via the **`agy-sh`** bridge:

### 2.1 Subsystem Developer Packages
The Alpine subsystem container provides the necessary build tools:
- `openjdk-21` (JVM launcher and compiler)
- `aapt2` (`/usr/bin/aapt2`, native ARM64 Android asset packaging tool)
- `patchelf` (>= 0.19.2)
- `zstd`, `tar`, `bash`, `git`, `python3`

If any tool is missing in the container, it can be installed via:
```bash
agy-sh -c "apk add --no-cache openjdk21-jre-headless aapt2 patchelf bash git python3"
```

### 2.2 Android SDK Configuration
Create or verify `local.properties` in the project root:
```properties
sdk.dir=/storage/emulated/0/Documents/AgyStudio/.android-sdk
```

The Android SDK contains:
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
android.aapt2FromMavenOverride=/usr/bin/aapt2
android.suppressUnsupportedCompileSdk=35
```

> **Why `org.gradle.daemon=false` is required**: In the Android/PRoot container environment, persistent background Gradle daemons keep pipe descriptors open, which causes subshells and terminal sessions to hang indefinitely after builds finish. Disabling the daemon forces Gradle to run in single-use mode and exit immediately upon completion.
>
> **Why `android.aapt2FromMavenOverride=/usr/bin/aapt2` is required**: The Google Maven-distributed `aapt2` x86_64 binary cannot run on native Android ARM64. Pointing Gradle to `/usr/bin/aapt2` inside the Alpine container rootfs ensures fast, native resource compilation. **Note**: `/usr/bin/aapt2` exists inside the `agy-sh` container, NOT on the Android host filesystem!

### 2.4 Signing Keystore
Release APKs are signed with a persistent `debug.keystore`:
- Location: `.android/debug.keystore` (or `~/.android/debug.keystore`)
- Password: `android`
- Key Alias: `androiddebugkey`
- Key Password: `android`

Gradle's build script automatically discovers keystores in `.android/` or `~/.android/`.

---

## 3. Decoupled Antigravity CLI (`agy`) Engine Architecture (v1.7.0)

In Antigravity Studio v1.7.0+, the CLI engine is **unbundled** from the APK, slashing APK size from ~180MB down to ~35MB.

### 3.1 Architecture Overview
* **Native Launcher Bridge (`libagy_launcher.so`)**: Packaged in `app/src/main/jniLibs/arm64-v8a/libagy_launcher.so` (~49KB). The bootstrapper symlinks `$usr/bin/agy` to this native binary.
* **On-Demand Engine Storage**: Downloaded directly on-demand to `/data/data/ai.agy.app/files/usr/opt/agy/agy`.
* **Zero-Mutation Execution**: Bypasses on-device `patchelf`. When `agy` is called, `libagy_launcher.so` directly invokes Glibc's dynamic linker:
  ```bash
  /data/data/ai.agy.app/files/usr/glibc/lib/ld-linux-aarch64.so.1 \
    --library-path /data/data/ai.agy.app/files/usr/glibc/lib \
    /data/data/ai.agy.app/files/usr/opt/agy/agy [args...]
  ```
  This runs at 100% native ARM64 speed, completely satisfies Android SELinux `W^X` policies (since the dynamic linker is in `nativeLibraryDir`), and preserves the upstream byte-for-byte SHA256 integrity.

### 3.2 Upstream Source & Lineage
* **Official Google Manifest**: `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_arm64.json`
* **Google Cloud Storage CDN**: Official Linux ARM64 binary (`cli_linux_arm64.tar.gz`) ingested directly from Google Cloud Storage with streaming SHA-512 verification.
* **On-Device VA39 Patching**: The app applies the TCMalloc 39-bit Virtual Address patch and seccomp syscall translation autonomously in Python on-device before staging.
* The engine can be downloaded, updated, or repaired independently of the app via **Settings [⚙] → Software Updates → Antigravity CLI Engine**.

### 3.3 Verification & Build Task
* The Gradle task `verifyLibAgy` executes `bash scripts/verify_or_patch_libagy.sh` before `preBuild`.
* It verifies that `libagy_launcher.so` is compiled from `app/src/main/cpp/agy_launcher.c` and ensures `libagy.so` is never bundled in the APK.

---

## 4. Step-by-Step Guide: Building the APK

Follow these steps to build and stage an updated release APK:

### Step 1: Pre-Build Invariants Verification
Run the architectural invariants validator and verify the native launcher bridge:
```bash
python3 scripts/verify_architectural_invariants.py
bash scripts/verify_or_patch_libagy.sh
```

### Step 2: Bump Version in `version.properties`
For Android to install an update in-place without requiring the user to uninstall the previous version, `VERSION_CODE` **must be strictly greater** than the currently installed build:

Edit [`version.properties`](file:///storage/emulated/0/Documents/AgyStudio/version.properties):
```properties
VERSION_CODE=94
VERSION_NAME=1.9.0
AGY_VERSION=1.5.1
AGY_SHA256=...
```

### Step 3: Run the Gradle Release Build
> [!IMPORTANT]
> **Mandatory Subsystem Invocation via `agy-sh`!**
> Gradle, OpenJDK 21, and native ARM64 `aapt2` reside inside the Alpine Linux container subsystem (`agy-sh`).
> Furthermore, because the repository is located on Android FUSE shared storage (`/storage/emulated/0`), running bare `./gradlew` on the host will fail with `Permission denied` and missing toolchains.
> **Always execute the build through the `agy-sh` container bridge using `bash ./gradlew`!**
> Bound max workers and disable the background daemon to avoid hangs and triggering Android's Phantom Process Killer:

```bash
agy-sh -c "cd /storage/emulated/0/Documents/AgyStudio && bash ./gradlew assembleRelease --max-workers=2 --no-daemon"
```

During this build, Gradle will automatically:
1. Execute `verifyLibAgy` to validate `libagy_launcher.so` and ensure no bulky engine binary is bundled.
2. Execute `syncWebAssets` to repackage latest `agy-ui/` files into `usr-data.tar.zst`.
3. Compile Kotlin and Java sources.
4. Package native libraries and assets into `app-release.apk`.
5. Sign the APK using the persistent keystore.

### Step 4: Verify & Stage the Output APK
Verify that the APK generated successfully (inside `agy-sh` or host):
```bash
agy-sh -c "aapt dump badging /storage/emulated/0/Documents/AgyStudio/app/build/outputs/apk/release/app-release.apk | head -n 3"
```

Stage the APK to the user's **Downloads** folder for one-tap installation:
```bash
cp -f app/build/outputs/apk/release/app-release.apk /storage/emulated/0/Download/AntigravityStudio.apk
cp -f app/build/outputs/apk/release/app-release.apk /storage/emulated/0/Download/AntigravityStudio-v1.9.0.apk
```

---

## 5. Troubleshooting & Common Pitfalls

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **`./gradlew: Permission denied`** | Shared storage (`/storage/emulated/0`) lacks POSIX execution bits (`noexec`), or ran on host without `agy-sh`. | Always run through the container bridge: `agy-sh -c "cd /storage/emulated/0/Documents/AgyStudio && bash ./gradlew <tasks>"`. |
| **Git reports every file modified or "dubious ownership"** | Android FUSE assigns fixed permissions. | Run `git config core.fileMode false` and `git config --global --add safe.directory /storage/emulated/0/Documents/AgyStudio`. |
| **"CLI Engine Missing / Required" on launch** | The CLI engine is decoupled from the APK (v1.7.0+) to minimize APK footprint. | Open **Settings [⚙] → Software Updates → Antigravity CLI Engine** to download and setup the engine (~58MB). |
| **AAPT2 `No such file or directory` or crash** | Gradle was executed directly on the host shell where `/usr/bin/aapt2` is absent. Inside the `agy-sh` container, `/usr/bin/aapt2` is pre-installed. | Execute the build inside the container: `agy-sh -c "cd /storage/emulated/0/Documents/AgyStudio && bash ./gradlew assembleRelease --max-workers=2 --no-daemon"`. |
| **"App not installed as package appears to be invalid"** | `VERSION_CODE` was not incremented, or signature keystore differed from previous installation. | Bump `VERSION_CODE` in `version.properties` and verify `.android/debug.keystore` is used. |
| **Build process killed abruptly / Out of Memory** | Android Phantom Process Killer killed high-concurrency Gradle worker pool. | Pass `--max-workers=2` or `--no-daemon` to `bash ./gradlew`. |
| **`Permission denied` when creating sockets or symlinks** | Attempted to create UNIX domain sockets or symlinks on FUSE storage. | Use internal ext4 storage (`$TMPDIR` or `/data/data/ai.agy.app/files/usr/tmp`). |

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
