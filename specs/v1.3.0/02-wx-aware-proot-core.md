# Specification 02: W^X-Aware PRoot Tracer Core & Syscall Chaining

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Component**: `libproot.so`  
> **Source Location**: `app/src/main/cpp/proot/src/`  
> **Destination**: `app/src/main/jniLibs/arm64-v8a/libproot.so`  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Audit Status**: Hardened per Argon Review (`ARGON-V13-07`, `ARGON-V13-12`, `ARGON-V13-16`)

---

## 1. Overview & Architectural Role

While `libproot_loader.so` (Spec 01) handles loading the main executable and its dynamic linker (`ld.so`) at `execve()` time, running real programs involves dynamically loading shared libraries (`.so`) via `DT_NEEDED` and `dlopen()`.

When a program calls:
```c
dlopen("libssl.so", RTLD_NOW);
```
The guest dynamic linker invokes `mmap()` with `PROT_READ | PROT_EXEC` and `fd >= 0`. Without intervention, the kernel LSM hook blocks this with `-EACCES`.

`libproot.so` provides the user-space tracer that intercepts these executable mappings and transparently chains them into anonymous mappings, allowing 100% of standard Linux `.so` libraries to load without kernel faults.

---

## 2. Verified Seccomp BPF Filter (`src/tracee/seccomp.c`)

### Critical Mitigations Integrated (`ARGON-V13-07`):
* **Sign-Bit File Descriptor Test**: In classic BPF, `BPF_JGE` is strictly unsigned 32-bit (`(uint32_t)fd >= 0`), making `fd == -1` (`0xFFFFFFFF >= 0`) evaluate to `true` and trapping all anonymous JIT `mmap` calls. We test the high sign-bit (`0x80000000U`) instead.
* **Filter Jump Offset Accuracy**: Formulas strictly follow `target = pc + 1 + offset`.
* **`SYS_mprotect` Interception**: Captures `SYS_mprotect` calls with `PROT_EXEC` so `-EACCES` recovery works.

```c
/*
 * Verified Seccomp cBPF Filter for W^X-Aware Executable Mappings (arm64)
 * Formula for jump targets: target_index = current_index + 1 + offset
 */
struct sock_filter wx_mmap_filter[] = {
    /* [0] Load syscall number */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),

    /* [1] If nr == SYS_mmap (222), go to [2]; else jump +6 to [8] */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_mmap, 0, 6),
    /* [2] Load arg2: prot (low 32 bits on little-endian arm64) */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[2])),
    /* [3] If (prot & PROT_EXEC), go to [4]; else jump +7 to [11] (ALLOW) */
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, PROT_EXEC, 0, 7),
    /* [4] Load arg3: flags */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[3])),
    /* [5] If (flags & MAP_ANONYMOUS), jump +5 to [11] (ALLOW); else go to [6] */
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, MAP_ANONYMOUS, 5, 0),
    /* [6] Load arg4: fd (32-bit signed int in low word) */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[4])),
    /* [7] If sign bit set (fd < 0), jump +3 to [11] (ALLOW); else jump +4 to [12] (TRACE) */
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x80000000U, 3, 4),

    /* [8] If nr == SYS_mprotect (226), go to [9]; else jump +2 to [11] (ALLOW) */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_mprotect, 0, 2),
    /* [9] Load arg2: prot */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[2])),
    /* [10] If (prot & PROT_EXEC), jump +1 to [12] (TRACE); else go to [11] (ALLOW) */
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, PROT_EXEC, 1, 0),

    /* [11] Fast path: zero-trap direct kernel execution */
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    /* [12] Slow path: trap into W^X-aware PRoot tracer */
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_TRACE),
};
```

---

## 3. Hardened Syscall Chaining & Result Restoration

### Critical Mitigations Integrated (`ARGON-V13-12`):
1. **`x0` Result Restoration**: When chaining `SYS_mprotect` after `SYS_mmap`, `mprotect`'s return value (`0`) would normally overwrite the tracee's `x0` register, causing the tracee to think `mmap()` returned `NULL`. We explicitly restore `poke_reg(tracee, SYSARG_RESULT, mapped_addr)`.
2. **Tracer-Assisted `mprotect` `-EACCES` Recovery**: If `mprotect(addr, len, PROT_READ | PROT_EXEC)` fails with `-EACCES` on a file-backed `PROT_READ` segment, PRoot reads the existing pages via `read_data()`, chains an anonymous `MAP_FIXED` replacement, writes the data back via `write_data()`, and applies `mprotect(PROT_READ | PROT_EXEC)`.

```c
/* In struct Tracee (src/tracee/tracee.h): */
struct {
    bool active;
    word_t orig_fd;
    word_t orig_off;
    word_t orig_prot;
    word_t length;
    word_t mapped_addr;
    uint8_t *saved_mprotect_bytes;
} wx_state;

/* In src/syscall/enter.c (case PR_mmap): */
word_t prot  = peek_reg(tracee, CURRENT, SYSARG_3);
word_t flags = peek_reg(tracee, CURRENT, SYSARG_4);
int    fd    = (int)peek_reg(tracee, CURRENT, SYSARG_5);

if ((prot & PROT_EXEC) && !(flags & MAP_ANONYMOUS) && fd >= 0) {
    tracee->wx_state.active    = true;
    tracee->wx_state.orig_fd   = (word_t)fd;
    tracee->wx_state.orig_off  = peek_reg(tracee, CURRENT, SYSARG_6);
    tracee->wx_state.orig_prot = prot;
    tracee->wx_state.length    = peek_reg(tracee, CURRENT, SYSARG_2);

    /* Mutate file-backed RX mmap into anonymous RW mmap */
    poke_reg(tracee, SYSARG_3, PROT_READ | PROT_WRITE);
    poke_reg(tracee, SYSARG_4, (flags & ~(MAP_DENYWRITE | MAP_EXECUTABLE)) | MAP_ANONYMOUS);
    poke_reg(tracee, SYSARG_5, (word_t)-1);
    poke_reg(tracee, SYSARG_6, 0);
}

/* In src/syscall/exit.c: */
case PR_mmap: {
    if (tracee->wx_state.active) {
        word_t res = peek_reg(tracee, CURRENT, SYSARG_RESULT);
        if ((ssize_t)res < 0) {
            tracee->wx_state.active = false;
            break;
        }
        tracee->wx_state.mapped_addr = res;

        /* Chain 1: Populate anonymous VMA from original file descriptor */
        register_chained_syscall(tracee, PR_pread64,
                                 tracee->wx_state.orig_fd,
                                 tracee->wx_state.mapped_addr,
                                 tracee->wx_state.length,
                                 tracee->wx_state.orig_off, 0, 0);

        /* Chain 2: Transition anonymous VMA to requested executable protection */
        register_chained_syscall(tracee, PR_mprotect,
                                 tracee->wx_state.mapped_addr,
                                 tracee->wx_state.length,
                                 tracee->wx_state.orig_prot, 0, 0, 0);

        /* Crucial: Force final return value in x0 back to mapped_addr! */
        force_chain_final_result(tracee, tracee->wx_state.mapped_addr);
        tracee->wx_state.active = false;
    } else if (tracee->wx_state.saved_mprotect_bytes != NULL) {
        /* Second stage of mprotect -EACCES recovery: anonymous RW VMA is now mapped */
        word_t res = peek_reg(tracee, CURRENT, SYSARG_RESULT);
        if ((ssize_t)res >= 0) {
            write_data(tracee, tracee->wx_state.mapped_addr,
                       tracee->wx_state.saved_mprotect_bytes,
                       tracee->wx_state.length);
            register_chained_syscall(tracee, PR_mprotect,
                                     tracee->wx_state.mapped_addr,
                                     tracee->wx_state.length,
                                     tracee->wx_state.orig_prot, 0, 0, 0);
            force_chain_final_result(tracee, 0);
        }
        TALLOC_FREE(tracee->wx_state.saved_mprotect_bytes);
    }
    break;
}

case PR_mprotect: {
    word_t res  = peek_reg(tracee, CURRENT, SYSARG_RESULT);
    word_t prot = peek_reg(tracee, ORIGINAL, SYSARG_3);
    if ((ssize_t)res == -EACCES && (prot & PROT_EXEC) &&
        tracee->wx_state.saved_mprotect_bytes == NULL) {
        word_t addr = peek_reg(tracee, ORIGINAL, SYSARG_1);
        word_t len  = peek_reg(tracee, ORIGINAL, SYSARG_2);

        /* Copy existing file-backed PROT_READ pages into tracer buffer */
        uint8_t *buf = talloc_size(tracee->ctx, len);
        if (buf != NULL && read_data(tracee, buf, addr, len) >= 0) {
            tracee->wx_state.mapped_addr          = addr;
            tracee->wx_state.length               = len;
            tracee->wx_state.orig_prot            = prot;
            tracee->wx_state.saved_mprotect_bytes = buf;

            /* Replace file-backed VMA at addr with anonymous RW VMA */
            register_chained_syscall(tracee, PR_mmap,
                                     addr, len, PROT_READ | PROT_WRITE,
                                     MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS,
                                     (word_t)-1, 0);
        }
    }
    break;
}
```

---

## 4. Orphan Protection in `src/tracee/event.c` (`ARGON-V13-16`)

To guarantee child processes never survive in an unmonitored state if the user cancels a turn or the app foreground service stops:

```c
const unsigned long default_ptrace_options = (
    PTRACE_O_TRACESYSGOOD |
    PTRACE_O_TRACEFORK    |
    PTRACE_O_TRACEVFORK   |
    PTRACE_O_TRACEVFORKDONE |
    PTRACE_O_TRACEEXEC    |
    PTRACE_O_TRACECLONE   |
    PTRACE_O_TRACEEXIT    |
    PTRACE_O_EXITKILL
);
```

---

## 5. Build & Packaging Requirements

* **Compiler**: Android Bionic Clang (NDK r27+).
* **Page Alignment**: `-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384`.
* **Output Library**: `app/src/main/jniLibs/arm64-v8a/libproot.so`.
* **Symlink Assembly**: Synchronized in both `EnvironmentBootstrapper.kt` and `reconcileSymlinkFarmAndLock()`:
  - `"proot"` to `"libproot.so"`
  - `"agy-sh"` to `"libagy_sh.so"`
  - `"ask_question"` to `"libask_question.so"` (retained from v1.2.0)
