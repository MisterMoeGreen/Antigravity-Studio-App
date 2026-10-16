# Specification 02: Hardened C Virtualization Shim (`libagy_shim.so`)

## 1. Overview & Purpose

Standard glibc and developer tool binaries expect a standard POSIX filesystem layout where paths like `/bin/sh`, `/etc/resolv.conf`, `/etc/ssl/certs/ca-certificates.crt`, and `/tmp` exist in the root mount namespace.

Rather than using `PRoot` (which intercepts every system call via `ptrace` and degrades I/O performance by 15–40%), we implement an in-process **Dynamic Linker Interposition Shim** (`libagy_shim.so`) loaded via `LD_PRELOAD`.

### Critical Enhancements in this Specification:
1. **User-Space Shebang (`#!`) Parser**: When executing scripts (e.g. `./script.py`), the Linux kernel (`fs/binfmt_script.c`) handles `#!` in kernel space and looks up `/usr/bin/env` on Android's rootfs, failing with `ENOENT`. `libagy_shim.so` inspects executable files for `#!` in user-space before `real_execve()`, translating the interpreter path to `$APP_PREFIX/bin/...`.
2. **`posix_spawn` & `posix_spawnp` Hooks**: Hooked because Python 3.12 and modern Git invoke subprocesses via `posix_spawn` instead of bare `fork+execve`.
3. **Bionic/Glibc Linker Isolation**: Automatically strips `LD_PRELOAD` when execve-ing the Glibc binary (`libagy.so`), avoiding memory corruption between Bionic and Glibc dynamic linkers.
4. **Complete 64-bit LFS Aliases**: Implements `openat64`, `open64`, `fopen64`, `stat64`, `lstat64`, and `fstatat64`.

---

## 2. Virtualization Mapping Rules

| Intercepted Path | Translated Target Path | Rationale |
| :--- | :--- | :--- |
| `/bin/sh`, `/system/bin/sh`, `/bin/bash`, `/usr/bin/bash` | `/data/data/ai.agy.app/files/usr/bin/bash` | Forces `agy` subprocesses to execute within the full developer bash environment. |
| `/usr/bin/env`, `/bin/env` | `/data/data/ai.agy.app/files/usr/bin/env` | Ensures shebang lines (`#!/usr/bin/env python3`) resolve correctly. |
| `/usr/bin/*`, `/bin/*` | `/data/data/ai.agy.app/files/usr/bin/*` | Fallback lookup for standard Linux binary paths. |
| `/etc/resolv.conf` | `/data/data/ai.agy.app/files/usr/etc/resolv.conf` | Routes glibc/Go DNS resolution to the dynamic DNS file maintained by Android's `ConnectivityManager`. |
| `/etc/nsswitch.conf` | `/data/data/ai.agy.app/files/usr/etc/nsswitch.conf` | Directs glibc host lookups (`hosts: files dns`). |
| `/etc/hosts` | `/data/data/ai.agy.app/files/usr/etc/hosts` | Localhost mapping (`127.0.0.1 localhost`). |
| `/etc/gitconfig` | `/data/data/ai.agy.app/files/usr/etc/gitconfig` | Global git configuration (`http.sslCAInfo`, `safe.directory`). |
| `/etc/ssl/certs/ca-certificates.crt`, `/etc/pki/tls/certs/ca-bundle.crt` | `/data/data/ai.agy.app/files/usr/etc/tls/cert.pem` | Provides root CA certificates for Go, Node, Python, and Git network requests. |
| `/tmp`, `/var/tmp` | `/data/data/ai.agy.app/files/usr/tmp` | Provides an isolated writable temporary scratch directory. |
| `/tmp/*`, `/var/tmp/*` | `/data/data/ai.agy.app/files/usr/tmp/*` | Subpath mapping for temporary lockfiles and sockets. |

---

## 3. Complete Source Code (`app/src/main/cpp/agy_shim.c`)

```c
#define _GNU_SOURCE
#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define APP_PREFIX "/data/data/ai.agy.app/files/usr"
#define SHIM_PATH  "/data/data/ai.agy.app/files/usr/lib/libagy_shim.so"

extern char **environ;

/* Thread-safe path translation */
static inline const char* translate_path(const char* path, char* buf, size_t buf_size) {
    if (!path) return NULL;

    // Normalize multiple leading slashes (e.g. "//bin/sh")
    while (path[0] == '/' && path[1] == '/') {
        path++;
    }

    // Shell & env redirection
    if (strcmp(path, "/bin/sh") == 0 || strcmp(path, "/system/bin/sh") == 0 ||
        strcmp(path, "/bin/bash") == 0 || strcmp(path, "/usr/bin/bash") == 0 ||
        strcmp(path, "/usr/bin/sh") == 0) {
        return APP_PREFIX "/bin/bash";
    }
    if (strcmp(path, "/usr/bin/env") == 0 || strcmp(path, "/bin/env") == 0) {
        return APP_PREFIX "/bin/env";
    }

    // Standard /usr/bin/* or /bin/* lookup fallback into APP_PREFIX/bin/*
    if (strncmp(path, "/usr/bin/", 9) == 0) {
        snprintf(buf, buf_size, "%s/bin/%s", APP_PREFIX, path + 9);
        return buf;
    }
    if (strncmp(path, "/bin/", 5) == 0) {
        snprintf(buf, buf_size, "%s/bin/%s", APP_PREFIX, path + 5);
        return buf;
    }

    // Networking & DNS configuration
    if (strcmp(path, "/etc/resolv.conf") == 0) {
        return APP_PREFIX "/etc/resolv.conf";
    }
    if (strcmp(path, "/etc/nsswitch.conf") == 0) {
        return APP_PREFIX "/etc/nsswitch.conf";
    }
    if (strcmp(path, "/etc/hosts") == 0) {
        return APP_PREFIX "/etc/hosts";
    }
    if (strcmp(path, "/etc/gitconfig") == 0) {
        return APP_PREFIX "/etc/gitconfig";
    }

    // TLS Certificate Authorities
    if (strcmp(path, "/etc/ssl/certs/ca-certificates.crt") == 0 ||
        strcmp(path, "/etc/pki/tls/certs/ca-bundle.crt") == 0 ||
        strcmp(path, "/etc/ssl/cert.pem") == 0) {
        return APP_PREFIX "/etc/tls/cert.pem";
    }

    // Scratch and temporary storage
    if (strcmp(path, "/tmp") == 0 || strcmp(path, "/var/tmp") == 0) {
        return APP_PREFIX "/tmp";
    }
    if (strncmp(path, "/tmp/", 5) == 0) {
        snprintf(buf, buf_size, "%s/tmp/%s", APP_PREFIX, path + 5);
        return buf;
    }
    if (strncmp(path, "/var/tmp/", 9) == 0) {
        snprintf(buf, buf_size, "%s/tmp/%s", APP_PREFIX, path + 9);
        return buf;
    }

    return path;
}

/* Check if target executable is the Glibc-linked agy binary */
static inline int is_glibc_target(const char* path) {
    if (!path) return 0;
    size_t len = strlen(path);
    if (len >= 4 && strcmp(path + len - 4, "/agy") == 0) return 1;
    if (len >= 10 && strcmp(path + len - 10, "/libagy.so") == 0) return 1;
    if (strstr(path, "ld-linux-aarch64") != NULL) return 1;
    return 0;
}

/* Build sanitized envp: strips LD_PRELOAD when entering Glibc binary to prevent linker collision */
static char** prepare_envp(char* const envp[], int strip_preload) {
    char* const* src = envp ? envp : environ;
    size_t count = 0;
    while (src && src[count]) count++;

    char** new_env = (char**)calloc(count + 2, sizeof(char*));
    if (!new_env) return (char**)src;

    size_t j = 0;
    int has_preload = 0;
    for (size_t i = 0; i < count; i++) {
        if (strncmp(src[i], "LD_PRELOAD=", 11) == 0) {
            has_preload = 1;
            if (strip_preload) continue; // Strip LD_PRELOAD for Glibc binary
        }
        new_env[j++] = src[i];
    }
    if (!strip_preload && !has_preload) {
        new_env[j++] = (char*)("LD_PRELOAD=" SHIM_PATH);
    }
    new_env[j] = NULL;
    return new_env;
}

typedef int (*execve_fn)(const char*, char* const[], char* const[]);

/* Intercept execve + user-space shebang (#!) resolution */
int execve(const char* filename, char* const argv[], char* const envp[]) {
    static execve_fn real_execve = NULL;
    if (!real_execve) {
        real_execve = (execve_fn)dlsym(RTLD_NEXT, "execve");
    }
    if (!filename) {
        errno = EFAULT;
        return -1;
    }

    char path_buf[PATH_MAX];
    const char* target = translate_path(filename, path_buf, sizeof(path_buf));

    // Inspect target file for shebang (#!) before kernel binfmt_script sees it
    int fd = (int (*)(int, const char*, int, ...))dlsym(RTLD_NEXT, "openat")(
        AT_FDCWD, target, O_RDONLY | O_CLOEXEC, 0);
    if (fd >= 0) {
        char hdr[256];
        ssize_t n = read(fd, hdr, sizeof(hdr) - 1);
        close(fd);
        if (n >= 2 && hdr[0] == '#' && hdr[1] == '!') {
            hdr[n] = '\0';
            char* nl = strchr(hdr, '\n');
            if (nl) *nl = '\0';
            char* cr = strchr(hdr, '\r');
            if (cr) *cr = '\0';

            char* p = hdr + 2;
            while (*p && isspace((unsigned char)*p)) p++;
            if (*p == '/') {
                char* interp = p;
                while (*p && !isspace((unsigned char)*p)) p++;
                char* opt_arg = NULL;
                if (*p) {
                    *p++ = '\0';
                    while (*p && isspace((unsigned char)*p)) p++;
                    if (*p) opt_arg = p;
                }

                char interp_buf[PATH_MAX];
                const char* real_interp = translate_path(interp, interp_buf, sizeof(interp_buf));

                size_t argc = 0;
                while (argv && argv[argc]) argc++;
                char** new_argv = (char**)calloc(argc + 3, sizeof(char*));
                if (new_argv) {
                    size_t idx = 0;
                    new_argv[idx++] = (char*)real_interp;
                    if (opt_arg) new_argv[idx++] = opt_arg;
                    new_argv[idx++] = (char*)target;
                    for (size_t i = 1; i < argc; i++) {
                        new_argv[idx++] = argv[i];
                    }
                    new_argv[idx] = NULL;

                    char** adj_env = prepare_envp(envp, is_glibc_target(real_interp));
                    int ret = real_execve(real_interp, new_argv, adj_env);
                    int saved_err = errno;
                    if (adj_env != envp && adj_env != environ) free(adj_env);
                    free(new_argv);
                    errno = saved_err;
                    return ret;
                }
            }
        }
    }

    char** adj_env = prepare_envp(envp, is_glibc_target(target));
    int ret = real_execve(target, argv, adj_env);
    int saved_err = errno;
    if (adj_env != envp && adj_env != environ) free(adj_env);
    errno = saved_err;
    return ret;
}

/* Intercept posix_spawn (used by Python 3.12 subprocess & git) */
typedef int (*posix_spawn_fn)(pid_t*, const char*, const posix_spawn_file_actions_t*,
                              const posix_spawnattr_t*, char* const[], char* const[]);
int posix_spawn(pid_t* pid, const char* path,
                const posix_spawn_file_actions_t* file_actions,
                const posix_spawnattr_t* attrp,
                char* const argv[], char* const envp[]) {
    static posix_spawn_fn real_posix_spawn = NULL;
    if (!real_posix_spawn) {
        real_posix_spawn = (posix_spawn_fn)dlsym(RTLD_NEXT, "posix_spawn");
    }
    char buf[PATH_MAX];
    const char* target = translate_path(path, buf, sizeof(buf));
    char** adj_env = prepare_envp(envp, is_glibc_target(target));
    int ret = real_posix_spawn(pid, target, file_actions, attrp, argv, adj_env);
    if (adj_env != envp && adj_env != environ) free(adj_env);
    return ret;
}

/* Intercept posix_spawnp */
typedef int (*posix_spawnp_fn)(pid_t*, const char*, const posix_spawn_file_actions_t*,
                               const posix_spawnattr_t*, char* const[], char* const[]);
int posix_spawnp(pid_t* pid, const char* file,
                 const posix_spawn_file_actions_t* file_actions,
                 const posix_spawnattr_t* attrp,
                 char* const argv[], char* const envp[]) {
    static posix_spawnp_fn real_posix_spawnp = NULL;
    if (!real_posix_spawnp) {
        real_posix_spawnp = (posix_spawnp_fn)dlsym(RTLD_NEXT, "posix_spawnp");
    }
    char buf[PATH_MAX];
    const char* target = translate_path(file, buf, sizeof(buf));
    char** adj_env = prepare_envp(envp, is_glibc_target(target));
    int ret = real_posix_spawnp(pid, target, file_actions, attrp, argv, adj_env);
    if (adj_env != envp && adj_env != environ) free(adj_env);
    return ret;
}

/* Intercept openat & openat64 */
typedef int (*openat_fn)(int, const char*, int, ...);
int openat(int dirfd, const char* pathname, int flags, ...) {
    static openat_fn real_openat = NULL;
    if (!real_openat) real_openat = (openat_fn)dlsym(RTLD_NEXT, "openat");
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list args;
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_openat(dirfd, target, flags, mode);
}
int openat64(int dirfd, const char* pathname, int flags, ...) __attribute__((alias("openat")));

/* Intercept open & open64 */
typedef int (*open_fn)(const char*, int, ...);
int open(const char* pathname, int flags, ...) {
    static open_fn real_open = NULL;
    if (!real_open) real_open = (open_fn)dlsym(RTLD_NEXT, "open");
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list args;
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_open(target, flags, mode);
}
int open64(const char* pathname, int flags, ...) __attribute__((alias("open")));

/* Intercept fopen & fopen64 */
typedef FILE* (*fopen_fn)(const char*, const char*);
FILE* fopen(const char* pathname, const char* mode) {
    static fopen_fn real_fopen = NULL;
    if (!real_fopen) real_fopen = (fopen_fn)dlsym(RTLD_NEXT, "fopen");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_fopen(target, mode);
}
FILE* fopen64(const char* pathname, const char* mode) __attribute__((alias("fopen")));

/* Intercept access & faccessat */
typedef int (*access_fn)(const char*, int);
int access(const char* pathname, int mode) {
    static access_fn real_access = NULL;
    if (!real_access) real_access = (access_fn)dlsym(RTLD_NEXT, "access");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_access(target, mode);
}

typedef int (*faccessat_fn)(int, const char*, int, int);
int faccessat(int dirfd, const char* pathname, int mode, int flags) {
    static faccessat_fn real_faccessat = NULL;
    if (!real_faccessat) real_faccessat = (faccessat_fn)dlsym(RTLD_NEXT, "faccessat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_faccessat(dirfd, target, mode, flags);
}

/* Intercept stat, lstat, fstatat + 64-bit aliases */
typedef int (*stat_fn)(const char*, struct stat*);
int stat(const char* pathname, struct stat* statbuf) {
    static stat_fn real_stat = NULL;
    if (!real_stat) real_stat = (stat_fn)dlsym(RTLD_NEXT, "stat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_stat(target, statbuf);
}
int stat64(const char* pathname, struct stat* statbuf) __attribute__((alias("stat")));

typedef int (*lstat_fn)(const char*, struct stat*);
int lstat(const char* pathname, struct stat* statbuf) {
    static lstat_fn real_lstat = NULL;
    if (!real_lstat) real_lstat = (lstat_fn)dlsym(RTLD_NEXT, "lstat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_lstat(target, statbuf);
}
int lstat64(const char* pathname, struct stat* statbuf) __attribute__((alias("lstat")));

typedef int (*fstatat_fn)(int, const char*, struct stat*, int);
int fstatat(int dirfd, const char* pathname, struct stat* statbuf, int flags) {
    static fstatat_fn real_fstatat = NULL;
    if (!real_fstatat) real_fstatat = (fstatat_fn)dlsym(RTLD_NEXT, "fstatat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_fstatat(dirfd, target, statbuf, flags);
}
int fstatat64(int dirfd, const char* pathname, struct stat* statbuf, int flags) __attribute__((alias("fstatat")));
```

---

## 4. Compilation Instructions

The shim **must be compiled with Android Bionic Clang** (e.g. `clang` in Termux, or Android NDK `aarch64-linux-android28-clang`) so that it links against Android's system `libc.so` and can be preloaded safely into `python3`, `bash`, `git`, and child processes:

```bash
clang -shared -fPIC -O2 -Wall \
  app/src/main/cpp/agy_shim.c \
  -o app/src/main/jniLibs/arm64-v8a/libagy_shim.so \
  -ldl
strip --strip-unneeded app/src/main/jniLibs/arm64-v8a/libagy_shim.so
```
The resulting library is approximately **20 KB**.
