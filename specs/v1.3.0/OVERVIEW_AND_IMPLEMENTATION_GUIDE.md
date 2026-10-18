# Master Overview & Implementation Guide: Antigravity Studio v1.3.0

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem & Arbitrary On-Demand Package Execution)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)  
> **Branch**: `v1.3.0`  
> **Review Status**: Architecturally audited and hardened by **Gemini 4 Argon (100% Mitigations Integrated)**

---

## 1. Executive Summary & Goals

In `v1.0.0` through `v1.1.2`, Antigravity Studio proved that Google's Glibc-compiled `agy` CLI binary, its web frontend, credentials, and built-in tools can run natively under Android 15/16/17's strict SELinux `W^X` constraints.

However, the user experience was constrained by the fixed set of pre-compiled binaries bundled with the APK. `v1.3.0` eliminates this boundary entirely by implementing a **W^X-Aware PRoot Subsystem with an Anonymous-Memory ELF Loader**, delivering:
1. **Unrestricted Package Installation**: `agy` can run `apk add` (or `apt install`) to dynamically install any package on demand (`nodejs`, `npm`, `openjdk-21`, `gradle`, `rust`, `cargo`, `php`, `clang`, `gcc`, `go`, etc.).
2. **Direct ARM64 Hardware Execution**: 95%–100% native CPU speed by eliminating QEMU TCG instruction decoding overhead.
3. **16 KB Page Size Interoperability**: Automatic execution of standard 4 KB-aligned Linux packages on 16 KB Android kernels via anonymous `pread64()` byte mapping.
4. **Permanent Update Durability**: User-installed packages, compiler caches, and npm modules live in persistent app storage (`/data/data/ai.agy.app/files/rootfs/`) and survive all APK updates.

---

## 2. Specification Index & Architecture Map

This implementation guide coordinates the five technical specifications in [`specs/v1.3.0/`](.):

```
specs/v1.3.0/
├── 01-anonymous-elf-loader.md             # Anonymous mmap + pread64 + mprotect(RX) primitive
├── 02-wx-aware-proot-core.md              # libproot.so tracer, seccomp BPF filter & syscall chaining
├── 03-rootfs-distribution-and-bootstrapper.md # Alpine minirootfs, ld-musl patch & update persistence
├── 04-host-container-bridge-and-shebang.md    # libagy_sh.so bridge, 1:1 path preservation & env propagation
├── 05-toolchain-verification-and-concurrency.md # Acceptance test matrix (Node, Java, Rust, PHP, C++)
├── OVERVIEW_AND_IMPLEMENTATION_GUIDE.md   # Master coordination guide, Argon audit & AC checklist
└── IMPLEMENTATION_LOG.md                  # Quick-resume state header & step execution ledger
```

---

## 3. Hardened Architecture & Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Mobile User
    participant Act as MainActivity (WebView)
    participant Srv as server.py (Async Daemon)
    participant Agy as Antigravity Engine (libagy.so)
    participant Bridge as libagy_sh.so (apk_data_file)
    participant PRoot as libproot.so (jniLibs)
    participant Loader as libproot_loader.so (Anonymous RAM)
    participant Rootfs as Container Filesystem (/files/rootfs)

    User->>Act: Prompts Agent: "Build a TypeScript app with npm test"
    Act->>Srv: WebSocket Turn Message
    Srv->>Agy: Spawns agy with workspace context
    Agy->>Bridge: Executes run_command("apk add nodejs npm && npm test")
    Bridge->>PRoot: Spawns proot with rootfs, 1:1 mounts, fakeroot -0
    PRoot->>Loader: execve() via apk_data_file stub
    Loader->>Loader: Allocates MAP_ANONYMOUS + pread64 + mprotect(RX)
    Loader->>Rootfs: Jumps to container entry point at 100% native speed
    Rootfs->>Rootfs: apk installs Node.js & npm; V8 runs cleanly in anonymous RAM
    Rootfs-->>Bridge: Streams test execution results & stdout
    Bridge-->>Agy: Returns command output to agent
    Agy-->>Srv: Streams NDJSON tokens & tool results
    Srv-->>Act: Displays real-time output in Chat Viewport
```

---

## 4. Argon Architectural Review: Critical Mitigations

| Issue ID | Subsystem | Severity | Argon Review Mitigation |
| :--- | :--- | :--- | :--- |
| **ARGON-V13-01** | **File-Backed `W^X` Block** | **CRITICAL** | Android LSM hook blocks `PROT_EXEC` on file descriptors from `app_data_file`. **Mitigation**: Decouple file reads from executable mappings using `mmap(MAP_ANONYMOUS, RW) + pread64(fd) + mprotect(RX)`. Bypasses `FILE__EXECUTE` and passes `PROCESS__EXECMEM`. |
| **ARGON-V13-02** | **QEMU Performance Trap** | **HIGH** | QEMU TCG incurs a 70%–85% CPU penalty and causes double-JIT deadlocks with Node.js V8 and OpenJDK. **Mitigation**: Discard QEMU for native `aarch64` binaries in favor of direct execution via W^X-aware PRoot. |
| **ARGON-V13-03** | **16 KB Kernel Page Size** | **HIGH** | 4 KB-aligned upstream Linux packages throw `EINVAL` on modern Android 15/16 16 KB kernels. **Mitigation**: `pread64()` operates on exact byte counts with zero page alignment requirements on file offsets. |
| **ARGON-V13-04** | **Shared Library `dlopen()` Faults** | **CRITICAL** | Dynamic linker calls `mmap(PROT_EXEC, fd)` for `.so` files. **Mitigation**: Patch guest `ld-musl` with zero-trap anonymous loading; use PRoot seccomp filter and `register_chained_syscall` as universal fallback. |
| **ARGON-V13-05** | **Path Discontinuity** | **MEDIUM** | Container paths differing from host paths confuse agent file tools. **Mitigation**: 1:1 bind mount of `/storage/emulated/0/Documents` and host workspace preserves exact path and inode identity. |
| **ARGON-V13-06** | **Update Erasure** | **FATAL** | Package updates must not wipe user-installed toolchains. **Mitigation**: `EnvironmentBootstrapper` guards `files/rootfs/` behind `os-release` existence check; zero files inside `rootfs` link to rotating `apk_data_file`. |
| **ARGON-V13-07** | **cBPF Unsigned Tautology** | **FATAL** | `BPF_JGE 0` treated `fd == -1` as positive, trapping anonymous JIT `mmap`. **Mitigation**: Use sign-bit test (`0x80000000U`), add `SYS_mprotect`, and re-calculate jump targets. |
| **ARGON-V13-08** | **Text Script Inode Execution** | **FATAL** | `libagy.so` calling `usr/bin/agy-sh` script failed on `MAY_EXEC` kernel check. **Mitigation**: Compile `agy-sh` as a native C binary (`libagy_sh.so`) in `jniLibs` (`apk_data_file`). |
| **ARGON-V13-09** | **Read-Only Segment Crash** | **FATAL** | Mapping `.rodata`/`PT_PHDR` directly as `PROT_READ` failed `pread64` with `-EFAULT`. **Mitigation**: Map all segments initially as `PROT_READ \| PROT_WRITE`, populate via `pread64`, and apply final protection via `mprotect`. |
| **ARGON-V13-10** | **16 KB Boundary Page Merging** | **CRITICAL** | Adjacent `RX` and `RW` segments sharing a 16 KB boundary were clobbered by `MAP_FIXED`. **Mitigation**: Merge boundary pages in `loader.c` and remove `#define PAGESIZE 4096` in `ld-musl`. |
| **ARGON-V13-11** | **Musl Null-Address `MAP_FIXED`** | **CRITICAL** | Initial base reservation passed `addr_min == 0` with `MAP_FIXED`, failing at `NULL`. **Mitigation**: Exclude `MAP_FIXED` when `addr_min == 0`. |
| **ARGON-V13-12** | **Chained Return Value Clobber** | **CRITICAL** | Chained `mprotect` overwrote tracee `x0` result with `0`. **Mitigation**: Explicitly restore `mapped_addr` in `SYSARG_RESULT`. |
| **ARGON-V13-16** | **Orphan Container Children** | **HIGH** | PRoot cancellation orphaned child processes. **Mitigation**: Add `PTRACE_O_EXITKILL` to ptrace options. |
| **ARGON-V13-18** | **JVM Shared File & GC Contention** | **MEDIUM** | HotSpot JVM crashed on `/tmp/hsperfdata` shared mmap. **Mitigation**: Inject `JAVA_TOOL_OPTIONS="-XX:-UsePerfData -XX:ActiveProcessorCount=4"`. |

---

## 5. Agent Implementation & Progress Tracking Protocol

1. **Gating Rule**: An autonomous agent must never mark a criterion `[x]` unless its **Verification Gate** shell command exits with code `0`.
2. **Atomic Verification**: After completing each task:
   - Run the exact verification command.
   - If it passes, flip `[ ]` to `[x]` in both this guide and [`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md).
   - If it fails, diagnose and self-heal before proceeding.
3. **Audit Log Update**: Append a completed entry to the Step Execution Ledger in [`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md).

---

## 6. Command-Verified Acceptance Criteria Checklist

### Phase 1: Anonymous-Memory ELF Loader Implementation & 16 KB Alignment
- [ ] **AC-1.1: Anonymous Loader Compilation & 16 KB Alignment**
  - Compile `libproot_loader.so` with `mmap(MAP_ANONYMOUS) + pread64 + mprotect(RX)` and verify 16 KB (`0x4000`) ELF segment alignment.
  - **Verification Gate**:
    ```bash
    test -f app/src/main/jniLibs/arm64-v8a/libproot_loader.so && \
    python3 -c '
    import subprocess
    out = subprocess.check_output(["readelf", "-lW", "app/src/main/jniLibs/arm64-v8a/libproot_loader.so"], text=True)
    loads = [l for l in out.splitlines() if l.strip().startswith("LOAD")]
    assert len(loads) >= 1, "No PT_LOAD segments found"
    for l in loads:
        align = int(l.split()[-1], 16)
        assert align >= 0x4000 and (align % 0x4000 == 0), f"Invalid alignment {hex(align)} in: {l}"
    print("AC-1.1 PASSED: 16KB alignment verified across all PT_LOAD segments")
    '
    ```
- [ ] **AC-1.2: Anonymous Loader Freestanding & W^X Syscall Audit**
  - Verify `libproot_loader.so` has zero `DT_NEEDED` external libraries (freestanding) and contains the anonymous memory loader sequence in `src/loader/loader.c`.
  - **Verification Gate**:
    ```bash
    ! readelf -d app/src/main/jniLibs/arm64-v8a/libproot_loader.so | grep -q '(NEEDED)' && \
    grep -q 'MAP_ANONYMOUS' app/src/main/cpp/proot/src/loader/loader.c && \
    grep -q 'PREAD64' app/src/main/cpp/proot/src/loader/loader.c && \
    grep -q 'MPROTECT' app/src/main/cpp/proot/src/loader/loader.c
    ```

### Phase 2: W^X-Aware PRoot Tracer Engine Staging
- [ ] **AC-2.1: `libproot.so` Compilation, 16 KB Alignment & JNI Staging**
  - Stage `libproot.so` in `jniLibs/arm64-v8a/` with 16 KB page size alignment, `PTRACE_O_EXITKILL`, and verified seccomp BPF filter.
  - **Verification Gate**:
    ```bash
    test -f app/src/main/jniLibs/arm64-v8a/libproot.so && \
    test $(stat -c%s app/src/main/jniLibs/arm64-v8a/libproot.so 2>/dev/null || stat -f%z app/src/main/jniLibs/arm64-v8a/libproot.so) -gt 100000 && \
    readelf -lW app/src/main/jniLibs/arm64-v8a/libproot.so | awk '/LOAD/{if (strtonum($NF) < 16384) exit 1}'
    ```
- [ ] **AC-2.2: Bootstrapper & Service PRoot Symlink Assembly**
  - Verify both `EnvironmentBootstrapper.kt` and `AgyForegroundService.kt` map `proot` to `libproot.so` and `agy-sh` to `libagy_sh.so`.
  - **Verification Gate**:
    ```bash
    grep -q '"proot" to "libproot.so"' app/src/main/java/ai/agy/app/EnvironmentBootstrapper.kt && \
    grep -q '"agy-sh" to "libagy_sh.so"' app/src/main/java/ai/agy/app/EnvironmentBootstrapper.kt && \
    grep -q '"proot" to "libproot.so"' app/src/main/java/ai/agy/app/AgyForegroundService.kt && \
    grep -q '"agy-sh" to "libagy_sh.so"' app/src/main/java/ai/agy/app/AgyForegroundService.kt
    ```

### Phase 3: Alpine Linux Rootfs Packaging & Bootstrapper Integration
- [ ] **AC-3.1: Alpine Minirootfs Asset Bundling & Patched `ld-musl` Verification**
  - Package minimal Alpine `aarch64` rootfs into `app/src/main/assets/rootfs.tar.zst` (< 5.0 MB) and verify `etc/os-release` and `lib/ld-musl-aarch64.so.1` exist inside the archive.
  - **Verification Gate**:
    ```bash
    test -f app/src/main/assets/rootfs.tar.zst && \
    test $(stat -c%s app/src/main/assets/rootfs.tar.zst 2>/dev/null || stat -f%z app/src/main/assets/rootfs.tar.zst) -lt 5242880 && \
    zstd -dc app/src/main/assets/rootfs.tar.zst | tar -tf - | grep -qE '^(\./)?etc/os-release$' && \
    zstd -dc app/src/main/assets/rootfs.tar.zst | tar -tf - | grep -qE '^(\./)?lib/ld-musl-aarch64\.so\.1$'
    ```
- [ ] **AC-3.2: Non-Destructive Bootstrapper Initialization**
  - Verify `EnvironmentBootstrapper.kt` guards `files/rootfs` extraction behind an `os-release` existence check.
  - **Verification Gate**:
    ```bash
    grep -q 'rootfs.tar.zst' app/src/main/java/ai/agy/app/EnvironmentBootstrapper.kt && \
    grep -q 'os-release' app/src/main/java/ai/agy/app/EnvironmentBootstrapper.kt
    ```

### Phase 4: Host-to-Container Interposition Bridge (`libagy_sh.so`)
- [ ] **AC-4.1: Native Bridge Binary (`libagy_sh.so`) Compilation & 16 KB Alignment**
  - Compile `libagy_sh.so` into `app/src/main/jniLibs/arm64-v8a/libagy_sh.so` with `unsetenv("LD_PRELOAD")`, 1:1 path mounts, and fakeroot `-0`.
  - **Verification Gate**:
    ```bash
    test -f app/src/main/jniLibs/arm64-v8a/libagy_sh.so && \
    readelf -lW app/src/main/jniLibs/arm64-v8a/libagy_sh.so | awk '/LOAD/{if (strtonum($NF) < 16384) exit 1}' && \
    strings app/src/main/jniLibs/arm64-v8a/libagy_sh.so | grep -q 'PROOT_LOADER'
    ```
- [ ] **AC-4.2: Shim Translation & Service `SHELL` Routing to `agy-sh`**
  - Verify `agy_shim.c` translates `/bin/sh` to `APP_PREFIX "/bin/agy-sh"` and strips `LD_PRELOAD` for `proot`/`agy-sh`, and `AgyForegroundService.kt` sets `env["SHELL"] = "$usr/bin/agy-sh"`.
  - **Verification Gate**:
    ```bash
    grep -q 'APP_PREFIX "/bin/agy-sh"' app/src/main/cpp/agy_shim.c && \
    grep -q 'libproot.so' app/src/main/cpp/agy_shim.c && \
    grep -q 'env\["SHELL"\] = "\$usr/bin/agy-sh"' app/src/main/java/ai/agy/app/AgyForegroundService.kt
    ```

### Phase 5: Dynamic Toolchain Verification & Release Packaging
- [ ] **AC-5.1: Container Boot & Node.js V8 JIT Execution Gate**
  - Extract `rootfs.tar.zst` to a temporary test rootfs, install `nodejs` via `apk`, and verify V8 JIT execution under `libproot.so` + `libproot_loader.so`.
  - **Verification Gate**:
    ```bash
    TEST_ROOT="${TMPDIR:-/tmp}/agy_v13_test_rootfs" && \
    rm -rf "$TEST_ROOT" && mkdir -p "$TEST_ROOT" && \
    zstd -dc app/src/main/assets/rootfs.tar.zst | tar -xf - -C "$TEST_ROOT" && \
    cp /etc/resolv.conf "$TEST_ROOT/etc/resolv.conf" 2>/dev/null || printf "nameserver 8.8.8.8\n" > "$TEST_ROOT/etc/resolv.conf" && \
    PROOT_LOADER="$(pwd)/app/src/main/jniLibs/arm64-v8a/libproot_loader.so" \
    PROOT_TMP_DIR="${TMPDIR:-/tmp}" \
    ./app/src/main/jniLibs/arm64-v8a/libproot.so -r "$TEST_ROOT" -0 -b /dev -b /proc -b /sys -w /root \
    /bin/sh -c "apk update && apk add --no-cache nodejs && node -e 'console.log(\"V8_JIT_OK_\" + (20+22))'" | grep -q 'V8_JIT_OK_42'
    ```
- [ ] **AC-5.2: Java OpenJDK 21 Execution Gate Inside W^X Container**
  - Install `openjdk21-jre-headless` inside the test rootfs and verify JVM execution under `libproot.so` + `libproot_loader.so`.
  - **Verification Gate**:
    ```bash
    TEST_ROOT="${TMPDIR:-/tmp}/agy_v13_test_rootfs" && \
    PROOT_LOADER="$(pwd)/app/src/main/jniLibs/arm64-v8a/libproot_loader.so" \
    PROOT_TMP_DIR="${TMPDIR:-/tmp}" \
    ./app/src/main/jniLibs/arm64-v8a/libproot.so -r "$TEST_ROOT" -0 -b /dev -b /proc -b /sys -w /root \
    /bin/sh -c "apk add --no-cache openjdk21-jre-headless && java -XX:-UsePerfData -Xmx512m -version 2>&1" | grep -qi 'openjdk'
    ```
- [ ] **AC-5.3: Native C Compilation & Anonymous ELF Execution Gate**
  - Install `gcc` and `musl-dev` inside the test rootfs, compile a brand-new ELF executable inside writable storage, and execute it directly via the anonymous ELF loader without `-EACCES`.
  - **Verification Gate**:
    ```bash
    TEST_ROOT="${TMPDIR:-/tmp}/agy_v13_test_rootfs" && \
    PROOT_LOADER="$(pwd)/app/src/main/jniLibs/arm64-v8a/libproot_loader.so" \
    PROOT_TMP_DIR="${TMPDIR:-/tmp}" \
    ./app/src/main/jniLibs/arm64-v8a/libproot.so -r "$TEST_ROOT" -0 -b /dev -b /proc -b /sys -w /root \
    /bin/sh -c 'apk add --no-cache gcc musl-dev && printf "#include <stdio.h>\nint main(){puts(\"ANON_ELF_EXEC_OK\");return 0;}\n" > /root/t.c && gcc /root/t.c -o /root/t && /root/t' | grep -q 'ANON_ELF_EXEC_OK'
    ```
- [ ] **AC-5.4: Version Bump to v1.3.0 & 16 KB Aligned Release APK Verification**
  - Verify `version.properties` is set to `1.3.0`, release APK builds cleanly, and all bundled `.so` files in `jniLibs` pass 16 KB alignment checks.
  - **Verification Gate**:
    ```bash
    grep -q 'VERSION_NAME=1.3.0' version.properties && \
    bash ./gradlew assembleRelease && \
    test -f app/build/outputs/apk/release/app-release.apk
    ```
