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
