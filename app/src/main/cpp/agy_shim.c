#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
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
        strcmp(path, "/usr/bin/sh") == 0) {
        return APP_PREFIX "/bin/agy-sh";
    }
    if (strcmp(path, "/bin/bash") == 0 || strcmp(path, "/usr/bin/bash") == 0) {
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

/* Check if target executable is Glibc binary or PRoot / agy-sh container bridge */
static inline int is_glibc_target(const char* path) {
    if (!path) return 0;
    if (strcmp(path, "agy") == 0 || strcmp(path, "agy.va39") == 0 ||
        strcmp(path, "agy.patched") == 0) return 1;
    if (strcmp(path, "proot") == 0 || strcmp(path, "agy-sh") == 0) return 1;
    size_t len = strlen(path);
    if (len >= 4 && strcmp(path + len - 4, "/agy") == 0) return 1;
    if (len >= 6 && strcmp(path + len - 6, "/proot") == 0) return 1;
    if (len >= 7 && strcmp(path + len - 7, "/agy-sh") == 0) return 1;
    if (len >= 10 && strcmp(path + len - 10, "/libagy.so") == 0) return 1;
    if (len >= 11 && strcmp(path + len - 11, "/libproot.so") == 0) return 1;
    if (len >= 12 && strcmp(path + len - 12, "/libagy_sh.so") == 0) return 1;
    if (len >= 12 && strcmp(path + len - 12, "/agy.patched") == 0) return 1;
    if (len >= 18 && strcmp(path + len - 18, "/libask_question.so") == 0) return 1;
    if (len >= 19 && strcmp(path + len - 19, "/libagy_launcher.so") == 0) return 1;
    if (strstr(path, "/opt/agy/") != NULL) return 1;
    if (strstr(path, "ld-linux-aarch64") != NULL) return 1;
    return 0;
}

/* Build sanitized envp using caller's stack buffer when possible (vfork/Scudo-safe) */
static char** prepare_envp_buf(char* const envp[], int strip_preload,
                               char** stack_buf, size_t stack_cap, int* out_heap) {
    *out_heap = 0;
    char* const* src = envp ? envp : environ;
    size_t count = 0;
    while (src && src[count]) count++;

    char** new_env = stack_buf;
    if (count + 2 > stack_cap) {
        new_env = (char**)calloc(count + 2, sizeof(char*));
        if (!new_env) return (char**)src;
        *out_heap = 1;
    }

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

#ifndef AT_EMPTY_PATH
#define AT_EMPTY_PATH 0x1000
#endif

typedef int (*execve_fn)(const char*, char* const[], char* const[]);
typedef int (*execveat_fn)(int, const char*, char* const[], char* const[], int);
typedef int (*openat_fn)(int, const char*, int, ...);
typedef int (*posix_spawn_fn)(pid_t*, const char*, const posix_spawn_file_actions_t*,
                              const posix_spawnattr_t*, char* const[], char* const[]);

/* Extract PATH from explicit envp first, falling back to getenv("PATH") */
static const char* get_path_from_envp(char* const envp[]) {
    if (envp) {
        for (size_t i = 0; envp[i]; i++) {
            if (strncmp(envp[i], "PATH=", 5) == 0) {
                return envp[i][5] ? (envp[i] + 5) : (APP_PREFIX "/bin:/system/bin");
            }
        }
    }
    const char* p = getenv("PATH");
    return (p && *p) ? p : (APP_PREFIX "/bin:/system/bin");
}

/* Resolve a bare executable name against PATH (applying translate_path to candidates) */
static int resolve_in_path(const char* file, char* const envp[], char* out, size_t out_sz) {
    if (!file || !file[0] || strchr(file, '/') != NULL) return 0;

    const char* path_env = get_path_from_envp(envp);
    const char* cur = path_env;
    while (1) {
        const char* sep = strchr(cur, ':');
        size_t seg_len = sep ? (size_t)(sep - cur) : strlen(cur);

        char cand[PATH_MAX];
        int written;
        if (seg_len == 0) {
            written = snprintf(cand, sizeof(cand), "./%s", file);
        } else {
            while (seg_len > 1 && cur[seg_len - 1] == '/') seg_len--;
            written = snprintf(cand, sizeof(cand), "%.*s/%s", (int)seg_len, cur, file);
        }

        if (written > 0 && (size_t)written < sizeof(cand)) {
            char trans_buf[PATH_MAX];
            const char* resolved = translate_path(cand, trans_buf, sizeof(trans_buf));
            struct stat st;
            if (stat(resolved, &st) == 0 && !S_ISDIR(st.st_mode) && access(resolved, X_OK) == 0) {
                if (snprintf(out, out_sz, "%s", resolved) < (int)out_sz) {
                    return 1;
                }
            }
        }

        if (!sep) break;
        cur = sep + 1;
    }
    return 0;
}

/* Inspect target file for user-space shebang (#!) resolution; stack_argv avoids heap use on vfork */
static void resolve_shebang_args(const char* target, char* const argv[],
                                 char* hdr, size_t hdr_sz,
                                 char* interp_buf, size_t interp_sz,
                                 char** stack_argv, size_t stack_argv_cap,
                                 const char** out_target, char*** out_argv, int* out_argv_heap) {
    *out_target = target;
    *out_argv = NULL;
    *out_argv_heap = 0;
    if (!target || hdr_sz < 4) return;

    static openat_fn real_openat = NULL;
    if (!real_openat) real_openat = (openat_fn)dlsym(RTLD_NEXT, "openat");
    int fd = real_openat ? real_openat(AT_FDCWD, target, O_RDONLY | O_CLOEXEC, 0) : -1;
    if (fd < 0) return;

    ssize_t n = read(fd, hdr, hdr_sz - 1);
    close(fd);
    if (n < 2 || hdr[0] != '#' || hdr[1] != '!') return;

    hdr[n] = '\0';
    char* nl = strchr(hdr, '\n');
    if (nl) *nl = '\0';
    char* cr = strchr(hdr, '\r');
    if (cr) *cr = '\0';

    char* p = hdr + 2;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p != '/') return;

    char* interp = p;
    while (*p && !isspace((unsigned char)*p)) p++;
    char* opt_arg = NULL;
    if (*p) {
        *p++ = '\0';
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p) {
            opt_arg = p;
            char* end = opt_arg + strlen(opt_arg);
            while (end > opt_arg && isspace((unsigned char)end[-1])) {
                *--end = '\0';
            }
            if (!*opt_arg) opt_arg = NULL;
        }
    }

    const char* real_interp = translate_path(interp, interp_buf, interp_sz);
    size_t argc = 0;
    while (argv && argv[argc]) argc++;

    char** new_argv = stack_argv;
    if (argc + 3 > stack_argv_cap) {
        new_argv = (char**)calloc(argc + 3, sizeof(char*));
        if (!new_argv) return;
        *out_argv_heap = 1;
    }

    size_t idx = 0;
    new_argv[idx++] = (char*)real_interp;
    if (opt_arg) new_argv[idx++] = opt_arg;
    new_argv[idx++] = (char*)target;
    for (size_t i = 1; i < argc; i++) {
        new_argv[idx++] = argv[i];
    }
    new_argv[idx] = NULL;

    *out_target = real_interp;
    *out_argv = new_argv;
}

/* Execute an already path-resolved target with shebang resolution and LD_PRELOAD control */
static int execve_resolved(const char* target, char* const argv[], char* const envp[]) {
    static execve_fn real_execve = NULL;
    if (!real_execve) {
        real_execve = (execve_fn)dlsym(RTLD_NEXT, "execve");
    }

    char hdr[256];
    char interp_buf[PATH_MAX];
    char* stack_argv[64];
    char* stack_env[256];
    const char* exec_target = target;
    char** new_argv = NULL;
    int argv_heap = 0;
    int env_heap = 0;

    resolve_shebang_args(target, argv, hdr, sizeof(hdr),
                         interp_buf, sizeof(interp_buf),
                         stack_argv, 64, &exec_target, &new_argv, &argv_heap);

    char* const* final_argv = new_argv ? new_argv : argv;
    int strip_preload = is_glibc_target(target) || is_glibc_target(exec_target);
    char** adj_env = prepare_envp_buf(envp, strip_preload, stack_env, 256, &env_heap);
    int ret = real_execve(exec_target, final_argv, adj_env);
    int saved_err = errno;
    if (env_heap) free(adj_env);
    if (argv_heap) free(new_argv);
    errno = saved_err;
    return ret;
}

/* Intercept execve + user-space shebang (#!) resolution */
int execve(const char* filename, char* const argv[], char* const envp[]) {
    if (!filename) {
        errno = EFAULT;
        return -1;
    }
    char path_buf[PATH_MAX];
    const char* target = translate_path(filename, path_buf, sizeof(path_buf));
    return execve_resolved(target, argv, envp);
}

/* Intercept execv (Bionic libc calls internal execve directly without PLT on Android 17) */
int execv(const char* path, char* const argv[]) {
    return execve(path, argv, environ);
}

/* Intercept execvpe with PATH search + path translation + LD_PRELOAD control */
int execvpe(const char* file, char* const argv[], char* const envp[]) {
    if (!file) {
        errno = EFAULT;
        return -1;
    }
    if (strchr(file, '/') != NULL) {
        return execve(file, argv, envp);
    }
    char resolved[PATH_MAX];
    if (resolve_in_path(file, envp, resolved, sizeof(resolved))) {
        return execve_resolved(resolved, argv, envp);
    }
    errno = ENOENT;
    return -1;
}

/* Intercept execvp */
int execvp(const char* file, char* const argv[]) {
    return execvpe(file, argv, environ);
}

/* Shared variadic collector for execl/execlp/execle */
static char** collect_va_argv(const char* arg0, va_list* ap, char* stack_buf[], size_t stack_cap) {
    size_t idx = 0;
    size_t cap = stack_cap;
    char** vec = stack_buf;
    int on_heap = 0;

    vec[idx++] = (char*)arg0;
    if (arg0 != NULL) {
        char* arg;
        while ((arg = va_arg(*ap, char*)) != NULL) {
            if (idx + 1 >= cap) {
                size_t new_cap = cap * 2;
                char** grown = on_heap
                    ? (char**)realloc(vec, new_cap * sizeof(char*))
                    : (char**)malloc(new_cap * sizeof(char*));
                if (!grown) {
                    if (on_heap) free(vec);
                    return NULL;
                }
                if (!on_heap) memcpy(grown, vec, idx * sizeof(char*));
                vec = grown;
                cap = new_cap;
                on_heap = 1;
            }
            vec[idx++] = arg;
        }
        vec[idx] = NULL;
    }
    return vec;
}

int execl(const char* path, const char* arg0, ...) {
    char* stack_argv[64];
    va_list ap;
    va_start(ap, arg0);
    char** argv = collect_va_argv(arg0, &ap, stack_argv, 64);
    va_end(ap);
    if (!argv) {
        errno = ENOMEM;
        return -1;
    }
    int ret = execv(path, argv);
    int saved_err = errno;
    if (argv != stack_argv) free(argv);
    errno = saved_err;
    return ret;
}

int execlp(const char* file, const char* arg0, ...) {
    char* stack_argv[64];
    va_list ap;
    va_start(ap, arg0);
    char** argv = collect_va_argv(arg0, &ap, stack_argv, 64);
    va_end(ap);
    if (!argv) {
        errno = ENOMEM;
        return -1;
    }
    int ret = execvp(file, argv);
    int saved_err = errno;
    if (argv != stack_argv) free(argv);
    errno = saved_err;
    return ret;
}

int execle(const char* path, const char* arg0, ...) {
    char* stack_argv[64];
    va_list ap;
    va_start(ap, arg0);
    char** argv = collect_va_argv(arg0, &ap, stack_argv, 64);
    char* const* envp = argv ? va_arg(ap, char* const*) : NULL;
    va_end(ap);
    if (!argv) {
        errno = ENOMEM;
        return -1;
    }
    int ret = execve(path, argv, envp);
    int saved_err = errno;
    if (argv != stack_argv) free(argv);
    errno = saved_err;
    return ret;
}

/* Intercept execveat (runtime dlsym keeps Android 9 / API 28 compatibility) */
int execveat(int dirfd, const char* pathname, char* const argv[],
             char* const envp[], int flags) {
    if (pathname && flags == 0 && (dirfd == AT_FDCWD || pathname[0] == '/')) {
        return execve(pathname, argv, envp);
    }

    static execveat_fn real_execveat = NULL;
    if (!real_execveat) {
        real_execveat = (execveat_fn)dlsym(RTLD_NEXT, "execveat");
    }
    if (!real_execveat) {
        errno = ENOSYS;
        return -1;
    }

    char fd_target[PATH_MAX];
    char combined[PATH_MAX];
    const char* resolved_hint = pathname;
    if (dirfd != AT_FDCWD && (!pathname || pathname[0] != '/')) {
        char proc_fd[64];
        snprintf(proc_fd, sizeof(proc_fd), "/proc/self/fd/%d", dirfd);
        ssize_t len = readlink(proc_fd, fd_target, sizeof(fd_target) - 1);
        if (len > 0) {
            fd_target[len] = '\0';
            if ((flags & AT_EMPTY_PATH) || !pathname || pathname[0] == '\0') {
                resolved_hint = fd_target;
            } else if (snprintf(combined, sizeof(combined), "%s/%s", fd_target, pathname) < (int)sizeof(combined)) {
                resolved_hint = combined;
            }
        }
    }

    char path_buf[PATH_MAX];
    char* stack_env[256];
    int env_heap = 0;
    const char* target = pathname ? translate_path(pathname, path_buf, sizeof(path_buf)) : pathname;
    int is_glibc = is_glibc_target(resolved_hint) || is_glibc_target(target);
    char** adj_env = prepare_envp_buf(envp, is_glibc, stack_env, 256, &env_heap);
    int ret = real_execveat(dirfd, target, argv, adj_env, flags);
    int saved_err = errno;
    if (env_heap) free(adj_env);
    errno = saved_err;
    return ret;
}

/* Intercept fexecve (API 28+ Bionic invokes execveat syscall directly) */
int fexecve(int fd, char* const argv[], char* const envp[]) {
    return execveat(fd, "", argv, envp, AT_EMPTY_PATH);
}

/* Shared posix_spawn / posix_spawnp handler with PATH lookup, shebang resolution, and LD_PRELOAD control */
static int posix_spawn_common(int use_path, pid_t* pid, const char* file,
                              const posix_spawn_file_actions_t* file_actions,
                              const posix_spawnattr_t* attrp,
                              char* const argv[], char* const envp[]) {
    if (!file) return EINVAL;

    static posix_spawn_fn real_posix_spawn = NULL;
    if (!real_posix_spawn) {
        real_posix_spawn = (posix_spawn_fn)dlsym(RTLD_NEXT, "posix_spawn");
    }

    char path_buf[PATH_MAX];
    char* stack_env[256];
    int env_heap = 0;
    const char* target = NULL;

    if (use_path && strchr(file, '/') == NULL) {
        if (resolve_in_path(file, envp, path_buf, sizeof(path_buf))) {
            target = path_buf;
        }
    } else {
        target = translate_path(file, path_buf, sizeof(path_buf));
    }

    if (target) {
        char hdr[256];
        char interp_buf[PATH_MAX];
        char* stack_argv[64];
        const char* spawn_target = target;
        char** new_argv = NULL;
        int argv_heap = 0;
        resolve_shebang_args(target, argv, hdr, sizeof(hdr),
                             interp_buf, sizeof(interp_buf),
                             stack_argv, 64, &spawn_target, &new_argv, &argv_heap);

        char* const* final_argv = new_argv ? new_argv : argv;
        int strip_preload = is_glibc_target(target) || is_glibc_target(spawn_target);
        char** adj_env = prepare_envp_buf(envp, strip_preload, stack_env, 256, &env_heap);
        int ret = real_posix_spawn(pid, spawn_target, file_actions, attrp, final_argv, adj_env);
        if (env_heap) free(adj_env);
        if (argv_heap) free(new_argv);
        return ret;
    }

    // Fallback to libc posix_spawnp if unresolved (e.g. FUSE / external script edge case)
    static posix_spawn_fn real_posix_spawnp = NULL;
    if (!real_posix_spawnp) {
        real_posix_spawnp = (posix_spawn_fn)dlsym(RTLD_NEXT, "posix_spawnp");
    }
    char** adj_env = prepare_envp_buf(envp, is_glibc_target(file), stack_env, 256, &env_heap);
    int ret = real_posix_spawnp(pid, file, file_actions, attrp, argv, adj_env);
    if (env_heap) free(adj_env);
    return ret;
}

/* Intercept posix_spawn (used by Python 3.12+ subprocess & git) */
int posix_spawn(pid_t* pid, const char* path,
                const posix_spawn_file_actions_t* file_actions,
                const posix_spawnattr_t* attrp,
                char* const argv[], char* const envp[]) {
    return posix_spawn_common(0, pid, path, file_actions, attrp, argv, envp);
}

/* Intercept posix_spawnp */
int posix_spawnp(pid_t* pid, const char* file,
                 const posix_spawn_file_actions_t* file_actions,
                 const posix_spawnattr_t* attrp,
                 char* const argv[], char* const envp[]) {
    return posix_spawn_common(1, pid, file, file_actions, attrp, argv, envp);
}

/* Check if target path resides within protected system directories */
static int is_protected_target(int dirfd, const char* path) {
    if (!path) return 0;

    // Fast filter on protected basenames
    const char* base = strrchr(path, '/');
    const char* name = base ? (base + 1) : path;
    if (strcmp(name, "agy") != 0 &&
        strcmp(name, "agy.va39") != 0 &&
        strcmp(name, "libagy.so") != 0 &&
        strncmp(name, ".agy", 4) != 0 &&
        strncmp(name, "agy.tmp", 7) != 0) {
        return 0;
    }

    // Resolve full path using dirfd if relative
    char full_path[PATH_MAX];
    if (path[0] == '/') {
        snprintf(full_path, sizeof(full_path), "%s", path);
    } else {
        if (dirfd == AT_FDCWD) {
            if (!getcwd(full_path, sizeof(full_path))) return 1;
        } else {
            char fd_link[64];
            snprintf(fd_link, sizeof(fd_link), "/proc/self/fd/%d", dirfd);
            ssize_t r = readlink(fd_link, full_path, sizeof(full_path) - 1);
            if (r < 0) return 1;
            full_path[r] = '\0';
        }
        size_t cur = strlen(full_path);
        snprintf(full_path + cur, sizeof(full_path) - cur, "/%s", path);
    }

    // Strictly protect system bin/lib directories (avoid false positives on workspace folders)
    if (strstr(full_path, "/files/usr/bin/") != NULL ||
        strstr(full_path, "/files/usr/lib/") != NULL ||
        strstr(full_path, "/lib/arm64/") != NULL) {
        return 1;
    }
    return 0;
}

/* Intercept unlink & unlinkat */
typedef int (*unlink_fn)(const char*);
int unlink(const char* pathname) {
    if (is_protected_target(AT_FDCWD, pathname)) {
        errno = EROFS;
        return -1;
    }
    static unlink_fn real_unlink = NULL;
    if (!real_unlink) real_unlink = (unlink_fn)dlsym(RTLD_NEXT, "unlink");
    return real_unlink(pathname);
}

typedef int (*unlinkat_fn)(int, const char*, int);
int unlinkat(int dirfd, const char* pathname, int flags) {
    if (is_protected_target(dirfd, pathname)) {
        errno = EROFS;
        return -1;
    }
    static unlinkat_fn real_unlinkat = NULL;
    if (!real_unlinkat) real_unlinkat = (unlinkat_fn)dlsym(RTLD_NEXT, "unlinkat");
    return real_unlinkat(dirfd, pathname, flags);
}

/* Intercept rename, renameat & renameat2 */
typedef int (*rename_fn)(const char*, const char*);
int rename(const char* oldpath, const char* newpath) {
    if (is_protected_target(AT_FDCWD, oldpath) || is_protected_target(AT_FDCWD, newpath)) {
        errno = EROFS;
        return -1;
    }
    static rename_fn real_rename = NULL;
    if (!real_rename) real_rename = (rename_fn)dlsym(RTLD_NEXT, "rename");
    return real_rename(oldpath, newpath);
}

typedef int (*renameat_fn)(int, const char*, int, const char*);
int renameat(int olddirfd, const char* oldpath, int newdirfd, const char* newpath) {
    if (is_protected_target(olddirfd, oldpath) || is_protected_target(newdirfd, newpath)) {
        errno = EROFS;
        return -1;
    }
    static renameat_fn real_renameat = NULL;
    if (!real_renameat) real_renameat = (renameat_fn)dlsym(RTLD_NEXT, "renameat");
    return real_renameat(olddirfd, oldpath, newdirfd, newpath);
}

typedef int (*renameat2_fn)(int, const char*, int, const char*, unsigned int);
int renameat2(int olddirfd, const char* oldpath, int newdirfd, const char* newpath, unsigned int flags) {
    if (is_protected_target(olddirfd, oldpath) || is_protected_target(newdirfd, newpath)) {
        errno = EROFS;
        return -1;
    }
    static renameat2_fn real_renameat2 = NULL;
    if (!real_renameat2) real_renameat2 = (renameat2_fn)dlsym(RTLD_NEXT, "renameat2");
    return real_renameat2(olddirfd, oldpath, newdirfd, newpath, flags);
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
    if ((flags & (O_WRONLY | O_RDWR | O_CREAT | O_TRUNC | O_APPEND)) &&
        is_protected_target(dirfd, pathname)) {
        errno = EROFS;
        return -1;
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
    if ((flags & (O_WRONLY | O_RDWR | O_CREAT | O_TRUNC | O_APPEND)) &&
        is_protected_target(AT_FDCWD, pathname)) {
        errno = EROFS;
        return -1;
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

typedef int (*stat64_fn)(const char*, struct stat64*);
int stat64(const char* pathname, struct stat64* statbuf) {
    static stat64_fn real_stat64 = NULL;
    if (!real_stat64) real_stat64 = (stat64_fn)dlsym(RTLD_NEXT, "stat64");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_stat64(target, statbuf);
}

typedef int (*lstat_fn)(const char*, struct stat*);
int lstat(const char* pathname, struct stat* statbuf) {
    static lstat_fn real_lstat = NULL;
    if (!real_lstat) real_lstat = (lstat_fn)dlsym(RTLD_NEXT, "lstat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_lstat(target, statbuf);
}

typedef int (*lstat64_fn)(const char*, struct stat64*);
int lstat64(const char* pathname, struct stat64* statbuf) {
    static lstat64_fn real_lstat64 = NULL;
    if (!real_lstat64) real_lstat64 = (lstat64_fn)dlsym(RTLD_NEXT, "lstat64");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_lstat64(target, statbuf);
}

typedef int (*fstatat_fn)(int, const char*, struct stat*, int);
int fstatat(int dirfd, const char* pathname, struct stat* statbuf, int flags) {
    static fstatat_fn real_fstatat = NULL;
    if (!real_fstatat) real_fstatat = (fstatat_fn)dlsym(RTLD_NEXT, "fstatat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_fstatat(dirfd, target, statbuf, flags);
}

typedef int (*fstatat64_fn)(int, const char*, struct stat64*, int);
int fstatat64(int dirfd, const char* pathname, struct stat64* statbuf, int flags) {
    static fstatat64_fn real_fstatat64 = NULL;
    if (!real_fstatat64) real_fstatat64 = (fstatat64_fn)dlsym(RTLD_NEXT, "fstatat64");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_fstatat64(dirfd, target, statbuf, flags);
}

/* Intercept opendir, mkdir, mkdirat, readlink, readlinkat (Android 17 Bionic intra-libc bypasses) */
typedef DIR* (*opendir_fn)(const char*);
DIR* opendir(const char* name) {
    static opendir_fn real_opendir = NULL;
    if (!real_opendir) real_opendir = (opendir_fn)dlsym(RTLD_NEXT, "opendir");
    char buf[PATH_MAX];
    const char* target = translate_path(name, buf, sizeof(buf));
    return real_opendir(target);
}

typedef int (*mkdir_fn)(const char*, mode_t);
int mkdir(const char* pathname, mode_t mode) {
    static mkdir_fn real_mkdir = NULL;
    if (!real_mkdir) real_mkdir = (mkdir_fn)dlsym(RTLD_NEXT, "mkdir");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_mkdir(target, mode);
}

typedef int (*mkdirat_fn)(int, const char*, mode_t);
int mkdirat(int dirfd, const char* pathname, mode_t mode) {
    static mkdirat_fn real_mkdirat = NULL;
    if (!real_mkdirat) real_mkdirat = (mkdirat_fn)dlsym(RTLD_NEXT, "mkdirat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_mkdirat(dirfd, target, mode);
}

typedef ssize_t (*readlink_fn)(const char*, char*, size_t);
ssize_t readlink(const char* pathname, char* linkbuf, size_t bufsiz) {
    static readlink_fn real_readlink = NULL;
    if (!real_readlink) real_readlink = (readlink_fn)dlsym(RTLD_NEXT, "readlink");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_readlink(target, linkbuf, bufsiz);
}

typedef ssize_t (*readlinkat_fn)(int, const char*, char*, size_t);
ssize_t readlinkat(int dirfd, const char* pathname, char* linkbuf, size_t bufsiz) {
    static readlinkat_fn real_readlinkat = NULL;
    if (!real_readlinkat) real_readlinkat = (readlinkat_fn)dlsym(RTLD_NEXT, "readlinkat");
    char buf[PATH_MAX];
    const char* target = translate_path(pathname, buf, sizeof(buf));
    return real_readlinkat(dirfd, target, linkbuf, bufsiz);
}
