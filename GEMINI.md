# Antigravity Studio — Project Guide & Build Specifications

This document defines the architecture, environment requirements, upstream binary synchronization, and build procedures for **Antigravity Studio** (`ai.agy.app`).

---

## 1. Architecture & Geometry Overview

- **Package ID**: `ai.agy.app`
- **Byte Geometry**: Exactly 10 ASCII characters (`ai.agy.app`), identical in byte-length to `com.termux` (10 chars). This 1:1 length match allows byte-level binary patching across all compiled native binaries, glibc shared objects, and scripts without altering ELF file offsets.
- **Storage Subsystem**:
  - The repository resides on Android shared storage (`/storage/emulated/0/Documents/AgyStudio` or `~/storage/shared/Documents/AgyStudio`), backed by Android FUSE.
  - **CRITICAL**: Android shared storage is mounted `noexec` and does **not** support POSIX `chmod +x` or UNIX permissions (`0600`/`0700`). All scripts and wrappers (including `gradlew`) must always be executed explicitly through their interpreter (e.g., `bash ./gradlew`).
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

> **Note on `patchelf`**: Version `0.19.2` or later is mandatory. Older versions (such as `0.18.0`) corrupt the `DT_STRTAB` section on 200MB+ Go/ELF binaries when adjusting 16KB page alignment, causing instant `SIGSEGV` (exit code -11).

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
android.useAndroidX=true
kotlin.code.style=official
android.nonTransitiveRClass=true
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
android.suppressUnsupportedCompileSdk=35
```

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
2. Validates ELF header and segment mappings using `llvm-readelf` to ensure `DT_STRTAB` is within a valid `PT_LOAD` segment.
3. If missing or corrupted, copies `/data/data/com.termux/files/usr/bin/agy.va39`, applies 16KB page alignment (`patchelf --page-size 16384`), configures the Glibc dynamic interpreter and RPATH, and replaces `/data/data/com.termux` with `/data/data/ai.agy.app`.

This script is registered directly as the Gradle task `verifyLibAgy` and runs automatically before every build (`preBuild`).

To manually run the verification/patch script at any time:
```bash
bash scripts/verify_or_patch_libagy.sh
```

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
VERSION_CODE=39
VERSION_NAME=1.6.0
AGY_VERSION=1.4.12
AGY_SHA256=...
```

### Step 3: Run the Gradle Release Build
> [!IMPORTANT]
> **Always invoke via `bash ./gradlew`!**
> Because the repository is located on Android FUSE shared storage, running bare `./gradlew` will fail with `Permission denied`. Always use `bash ./gradlew`.

```bash
bash ./gradlew assembleRelease
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
cp -f app/build/outputs/apk/release/app-release.apk /storage/emulated/0/Download/AntigravityStudio-v1.6.0.apk
```

---

## 5. Troubleshooting & Common Pitfalls

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **`./gradlew: Permission denied`** | Shared storage (`/storage/emulated/0`) lacks POSIX execution bits (`noexec`). | Always run through the bash interpreter: `bash ./gradlew <tasks>`. |
| **`exit code -11` (SIGSEGV) in `agy`** | `libagy.so` was patched with a buggy patchelf or unaligned pages, causing `unable to parse DT_STRTAB: virtual address is not in any segment`. | Run `bash scripts/verify_or_patch_libagy.sh` to cleanly re-patch from `agy.va39`. |
| **"Google Authentication Required" lock sheet appears even when signed in** | `server.py` runs `agy models` on startup to discover models. If `agy` crashes with code -11, `models` is empty, triggering the auth lock sheet. | Fixing the `libagy.so` segfault resolves this automatically. |
| **AAPT2 daemon crash / `ExecException`** | Google's x86_64 AAPT2 was invoked instead of native ARM64 AAPT2. | Ensure `android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2` is set in `gradle.properties`. |
| **"App not installed as package appears to be invalid"** | `VERSION_CODE` was not incremented, or signature keystore differed from previous installation. | Bump `VERSION_CODE` in `version.properties` and verify `.android/debug.keystore` is used. |
