# Specification 01: Anonymous-Memory ELF Loader & 16 KB Boundary Alignment Bridge

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Component**: `libproot_loader.so`  
> **Source Location**: `app/src/main/cpp/proot/src/loader/loader.c`  
> **Destination**: `app/src/main/jniLibs/arm64-v8a/libproot_loader.so`  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Audit Status**: Hardened per Argon Review (`ARGON-V13-09`, `ARGON-V13-10`, `ARGON-V13-17`)

---

## 1. Problem Statement & Kernel SELinux LSM Boundary

Starting with Android 10 (API 29) and strictly enforced under `targetSdkVersion 35` (Android 15/16/17+), the Android kernel policy `untrusted_app_all.te` forbids executing files residing in the application's writable data directory (`/data/data/ai.agy.app/`):

```selinux
neverallow untrusted_app_all { file_type -apk_data_file -app_data_file }:file exec_mod;
```

When an unprivileged process attempts to execute or map an executable segment from an untrusted file descriptor, the Linux kernel LSM hook (`security/selinux/hooks.c: file_map_prot_check`) inspects:

```c
static int file_map_prot_check(struct file *file, unsigned long prot, int shared)
{
    const struct cred *cred = current_cred();
    u32 sid = cred_sid(cred);
    int rc = 0;

    if ((prot & PROT_EXEC) && (!file || (!shared && (prot & PROT_WRITE)))) {
        rc = avc_has_perm(sid, sid, SECCLASS_PROCESS, PROCESS__EXECMEM, NULL);
        if (rc)
            goto error;
    }

    if (file) {
        u32 av = FILE__READ;
        if (prot & PROT_EXEC)
            av |= FILE__EXECUTE;
        return file_has_perm(cred, file, av);
    }
    return 0;

error:
    return rc;
}
```

### The Architectural Flaw in Vanilla Loaders
Standard ELF loaders (including vanilla PRoot's `loader.c` and standard `ld.so`) map executable segments (`PT_LOAD` with `PF_X`) using:
```c
mmap(target_vaddr, segment_len, PROT_READ | PROT_EXEC, MAP_PRIVATE | MAP_FIXED, fd, segment_offset);
```
Because `file != NULL`, the kernel executes `if (file)`, checks `FILE__EXECUTE` on the `app_data_file` inode, and fails with `-EACCES`.

---

## 2. The Anonymous-Memory Loading Primitive

The Android SELinux policy explicitly grants untrusted apps the `PROCESS__EXECMEM` permission for anonymous JIT compilation:
```selinux
allow untrusted_app_all self:process execmem;
```

By decoupling the file read from the executable mapping, `libproot_loader.so` bypasses file-backed `W^X` enforcement completely:

```
[ Step 1: Anonymous Allocation ]
mmap(addr, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS, -1, 0)
└── VMA allocated with vma->vm_file == NULL

[ Step 2: Inode Read ]
pread64(fd, addr, filesz, offset)
└── Kernel checks only FILE__READ on app_data_file (Allowed!)

[ Step 3: Executable Transition ]
mprotect(addr, len, PROT_READ | PROT_EXEC)
└── vma->vm_file == NULL -> skips FILE__EXECUTE, checks only PROCESS__EXECMEM (Allowed!)
```

---

## 3. Hardened Implementation in `src/loader/loader.c`

### Critical Mitigations Integrated:
1. **Universal `PROT_READ | PROT_WRITE` Staging (`ARGON-V13-09`)**: Every segment (`RX`, `R`, or `RW`) must be mapped writable during `pread64` population. Mapping `.rodata` or `PT_PHDR` directly as `PROT_READ` and calling `pread64` causes `-EFAULT`.
2. **16 KB Runtime Page Alignment & Boundary Page Merging (`ARGON-V13-10`)**: On 16 KB kernels (`0x4000`), two adjacent 4 KB segments (`RX` and `RW`) may share a 16 KB boundary page. The loader avoids re-mapping an already-allocated boundary page with `MAP_FIXED` (which would clobber previous segment bytes) and upgrades the shared page's permissions to `last_mapped_page_prot | stmt->mmap.prot`.
3. **Freestanding Safe Zeroing (`ARGON-V13-17`)**: Replaces libc `bzero` with an explicit volatile byte-zeroing loop, and handles `-EINTR` in `pread64`.

```c
/*
 * Hardened 16KB-Page-Aware Anonymous ELF Segment Loader (Spec 01)
 * Compiles with: -shared -fPIC -O2 -Wall -nostdlib -ffreestanding
 *                -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384
 */
#define HOST_MAX_PAGE_SIZE 0x4000ULL /* 16384 bytes (covers both 4KB and 16KB kernels) */
#define HOST_PAGE_MASK     (~(HOST_MAX_PAGE_SIZE - 1ULL))

static inline void freestanding_zero(void *dst, word_t count) {
    volatile uint8_t *p = (volatile uint8_t *)dst;
    while (count--) {
        *p++ = 0;
    }
}

/* State tracked across PT_LOAD segments within the loader loop */
word_t last_mapped_page_end = 0;
word_t last_mapped_page_prot = 0;

/* Inside the load_script STATEMENT_MMAP / STATEMENT_MMAP_ANON handler: */
{
    word_t seg_start = stmt->mmap.addr;
    word_t seg_end   = stmt->mmap.addr + stmt->mmap.length;
    word_t map_start = seg_start & HOST_PAGE_MASK;
    word_t map_end   = (seg_end + HOST_MAX_PAGE_SIZE - 1ULL) & HOST_PAGE_MASK;

    word_t alloc_start = map_start;
    word_t shared_page = 0;

    /*
     * If a 4KB-aligned ELF has two adjacent PT_LOAD segments sharing the
     * same 16KB boundary page, do NOT re-mmap that shared 16KB page with
     * MAP_FIXED (which would clobber the previous segment's bytes!).
     * Instead, make the shared boundary page writable before pread64.
     */
    if (alloc_start < last_mapped_page_end) {
        shared_page = alloc_start;
        status = SYSCALL(MPROTECT, 3, shared_page, HOST_MAX_PAGE_SIZE,
                         PROT_READ | PROT_WRITE | (last_mapped_page_prot & PROT_EXEC));
        if ((ssize_t)status < 0) {
            return -1;
        }
        alloc_start = last_mapped_page_end;
    }

    /* 1. Allocate new 16KB-aligned anonymous RW pages for the rest of the segment */
    if (map_end > alloc_start) {
        status = SYSCALL(MMAP, 6, alloc_start, map_end - alloc_start,
                         PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS, (word_t)-1, 0);
        if ((void *)status == MAP_FAILED || (word_t)status != alloc_start) {
            return -1;
        }
    }

    /* 2. Populate exact segment bytes (even at 4KB-aligned seg_start) via pread64 */
    if (!anonymous && stmt->mmap.length > stmt->mmap.clear_length) {
        word_t filesz = stmt->mmap.length - stmt->mmap.clear_length;
        word_t total  = 0;
        while (total < filesz) {
            ssize_t n = (ssize_t)SYSCALL(PREAD64, 4, fd,
                                         seg_start + total,
                                         filesz - total,
                                         stmt->mmap.offset + total);
            if (n < 0) {
                if (n == -EINTR) continue;
                return -1;
            }
            if (n == 0) break; /* EOF reached */
            total += (word_t)n;
        }
    }

    /* 3. Zero BSS tail explicitly (essential if sharing a boundary page) */
    if (stmt->mmap.clear_length > 0) {
        word_t bss_start = seg_end - stmt->mmap.clear_length;
        freestanding_zero((void *)bss_start, stmt->mmap.clear_length);
    }

    /* 4. Apply final segment protections (RX, R, or RW) at 16KB page granularity */
    if (shared_page != 0) {
        word_t merged_prot = last_mapped_page_prot | stmt->mmap.prot;
        status = SYSCALL(MPROTECT, 3, shared_page, HOST_MAX_PAGE_SIZE, merged_prot);
        if ((ssize_t)status < 0) {
            return -1;
        }
    }
    if (map_end > alloc_start && stmt->mmap.prot != (PROT_READ | PROT_WRITE)) {
        status = SYSCALL(MPROTECT, 3, alloc_start, map_end - alloc_start, stmt->mmap.prot);
        if ((ssize_t)status < 0) {
            return -1;
        }
    }

    if (map_end > last_mapped_page_end) {
        last_mapped_page_end  = map_end;
        last_mapped_page_prot = stmt->mmap.prot;
    } else if (shared_page != 0) {
        last_mapped_page_prot |= stmt->mmap.prot;
    }
}
```

---

## 4. Build, Alignment & Packaging Specification

1. **Compilation**:
   Compiled using the Android NDK Bionic toolchain:
   ```bash
   $CC -shared -fPIC -O2 -Wall \
       -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
       -nostdlib -ffreestanding \
       src/loader/loader.c -o libproot_loader.so
   ```
2. **Packaging**:
   Staged into `app/src/main/jniLibs/arm64-v8a/libproot_loader.so` under `apk_data_file`.
3. **Runtime Propagation**:
   `AgyForegroundService` injects the absolute path into the environment:
   ```kotlin
   val nativeDir = context.applicationInfo.nativeLibraryDir
   env["PROOT_LOADER"] = "$nativeDir/libproot_loader.so"
   ```
   And `EnvironmentBootstrapper.kt` symlinks `usr/lib/libproot_loader.so` $\to$ `$nativeDir/libproot_loader.so`.
