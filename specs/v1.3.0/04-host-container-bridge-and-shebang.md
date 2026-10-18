# Specification 04: Host-to-Container Bridge & Native Execution Routing

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Components**: `app/src/main/cpp/agy_sh.c`, `app/src/main/cpp/agy_shim.c`, `AgyForegroundService.kt`  
> **Destination**: `app/src/main/jniLibs/arm64-v8a/libagy_sh.so` (Symlinked at `usr/bin/agy-sh`)  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Audit Status**: Hardened per Argon Review & Aligned with v1.2.0 Native Launcher Baseline

---

## 1. The Zero-Overhead Hybrid Topology

To maximize performance, Antigravity Studio separates agent internal operations from external shell tool execution:

```
[ Host Layer (Unconstrained) ]
• server.py (WebSocket Daemon & API Router)
• libagy.so (201 MB Go Engine: LLM Context, Tree Search, Tool Planner)
• Built-in Tools: view_file, edit_file, librg.so, libask_question.so (v1.2.0)
  └── Runs natively on Android Bionic host with ZERO ptrace overhead

[ Container Layer (W^X-Aware PRoot Subsystem) ]
• Invoked ONLY when agy calls run_command (/bin/sh -c "...")
• Provides full Linux environment: /bin, /usr/bin, apk, npm, node, gcc
• Direct ARM64 execution with Anonymous Memory Loader
• Connected to Host via UNIX Socket Bridge (ask_question.sock)
```

---

## 2. Native Bridge Executable (`app/src/main/cpp/agy_sh.c`)

### Critical Mitigations Integrated:
* **Bypassing the Text Script Inode Block (`ARGON-V13-08`)**: In modern Android, executing a text shell script (`#!/bin/sh`) stored inside `/data/data/...` fails at the kernel level (`MAY_EXEC` check on `app_data_file`). We compile `agy-sh` as a native C ELF binary (`libagy_sh.so`) staged in `jniLibs/arm64-v8a/` (`apk_data_file`), following the exact native ELF launcher pattern proven in `v1.2.0` by `libask_question.so`.
* **Sanitizing Bionic Preload Leakage**: `libagy_sh.so` strips `LD_PRELOAD`, `PYTHONHOME`, and `PYTHONPATH` so Bionic libraries do not crash the guest `musl` dynamic linker.
* **v1.2.0 Interactive Decision Bridge Propagation**: Exports `AGY_QUESTION_SOCK` and bind-mounts `/usr/local/bin/ask_question` and `usr/share/agy-ui` so interactive questions initiated from within the container communicate directly with `server.py`.

```c
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define APP_FILES "/data/data/ai.agy.app/files"
#define ROOTFS    APP_FILES "/rootfs"
#define PROOT_BIN APP_FILES "/usr/bin/proot"
#define LOADER_SO APP_FILES "/usr/lib/libproot_loader.so"

int main(int argc, char *argv[]) {
    /* 1. Prevent recursive container re-entry if already inside rootfs */
    if (access("/etc/alpine-release", F_OK) == 0 && access(PROOT_BIN, F_OK) != 0) {
        execv("/bin/sh", argv);
        return 127;
    }

    /* 2. Sanitize host Bionic environment variables before entering PRoot */
    unsetenv("LD_PRELOAD");
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    setenv("PROOT_TMP_DIR", APP_FILES "/usr/tmp", 1);
    setenv("PROOT_LOADER", LOADER_SO, 0);
    setenv("PROOT_NO_SECCOMP", "0", 0);

    /* 3. Propagate v1.2.0 interactive question IPC socket */
    setenv("AGY_QUESTION_SOCK", APP_FILES "/home/.gemini/antigravity-cli/ask_question.sock", 1);

    /* 4. Set mobile concurrency & memory guardrails (Spec 05) */
    setenv("CARGO_BUILD_JOBS", "4", 0);
    setenv("MAKEFLAGS", "-j4", 0);
    setenv("CMAKE_BUILD_PARALLEL_LEVEL", "4", 0);
    setenv("GRADLE_OPTS", "-Dorg.gradle.daemon=false -Dorg.gradle.workers.max=4 -Xmx1536m", 0);
    setenv("JAVA_TOOL_OPTIONS", "-XX:-UsePerfData -XX:ActiveProcessorCount=4 -Xmx1536m", 0);
    setenv("NODE_OPTIONS", "--max-old-space-size=1536", 0);
    setenv("HOME", "/root", 1);

    /* 5. Resolve current working directory with safe fallback */
    char cwd[PATH_MAX];
    if (!getcwd(cwd, sizeof(cwd))) {
        snprintf(cwd, sizeof(cwd), "%s", APP_FILES "/home/workspace");
    }

    /* 6. Construct proot argv with 1:1 host path identity mounts */
    char **new_argv = calloc((size_t)argc + 36, sizeof(char *));
    if (!new_argv) return 127;

    int k = 0;
    new_argv[k++] = PROOT_BIN;
    new_argv[k++] = "-r";
    new_argv[k++] = ROOTFS;
    new_argv[k++] = "-0";
    new_argv[k++] = "--link2symlink";
    new_argv[k++] = "-b"; new_argv[k++] = "/dev";
    new_argv[k++] = "-b"; new_argv[k++] = "/dev/pts";
    new_argv[k++] = "-b"; new_argv[k++] = "/proc";
    new_argv[k++] = "-b"; new_argv[k++] = "/sys";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/usr/etc/resolv.conf:/etc/resolv.conf";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/usr/tmp:/tmp";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/usr/tmp:/dev/shm";
    /* Preserve BOTH 1:1 host path (/data/data/ai.agy.app/files/home/...) AND /root/workspace */
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/home:" APP_FILES "/home";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/home/workspace:/root/workspace";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/home/.ssh:/root/.ssh";
    /* Bridge v1.2.0 ask_question tool into container */
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/usr/bin/ask_question:/usr/local/bin/ask_question";
    new_argv[k++] = "-b"; new_argv[k++] = APP_FILES "/usr/share/agy-ui:" APP_FILES "/usr/share/agy-ui";
    if (access("/storage/emulated/0", F_OK) == 0) {
        new_argv[k++] = "-b"; new_argv[k++] = "/storage/emulated/0:/storage/emulated/0";
    }
    new_argv[k++] = "-w";
    new_argv[k++] = cwd;
    /* Clean guest PATH and unset LD_LIBRARY_PATH inside the Alpine musl guest */
    new_argv[k++] = "/usr/bin/env";
    new_argv[k++] = "-u";
    new_argv[k++] = "LD_LIBRARY_PATH";
    new_argv[k++] = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.cargo/bin:/root/.local/bin";
    new_argv[k++] = "/bin/sh";

    for (int i = 1; i < argc; i++) {
        new_argv[k++] = argv[i];
    }
    new_argv[k] = NULL;

    execv(PROOT_BIN, new_argv);
    perror("agy-sh: execv(proot) failed");
    return 127;
}
```

---

## 3. Virtualization Shim Updates (`agy_shim.c`)

1. **Path Interception**: In `translate_path()`, route `/bin/sh` to `APP_PREFIX "/bin/agy-sh"`, while keeping `/bin/bash` pointing to the host Bionic bash.
2. **Stripping `LD_PRELOAD`**: In `is_glibc_target()`, strip `LD_PRELOAD` whenever entering `proot`, `libproot.so`, `agy-sh`, `libagy_sh.so`, or `libask_question.so`.

---

## 4. Service Shell Initialization (`AgyForegroundService.kt`)

`AgyForegroundService.kt` sets:
```kotlin
env["SHELL"] = "$usr/bin/agy-sh"
env["PROOT_LOADER"] = "$nativeDir/libproot_loader.so"
env["PROOT_TMP_DIR"] = "$usr/tmp"
```
When `libagy.so` spawns shell subcommands through `run_command`, it invokes `$SHELL -c "<command>"`, immediately launching `libagy_sh.so` and executing seamlessly inside the Linux rootfs.
