# Specification 03: Rootfs Distribution, Bootstrapper & Persistence

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Component**: `EnvironmentBootstrapper.kt` & Alpine Rootfs Assets  
> **Asset Path**: `app/src/main/assets/rootfs.tar.zst`  
> **Runtime Directory**: `/data/data/ai.agy.app/files/rootfs/`  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Audit Status**: Hardened per Argon Review (`ARGON-V13-10`, `ARGON-V13-11`, `ARGON-V13-15`)

---

## 1. Base Rootfs Selection: Alpine Linux `aarch64` Minirootfs

To keep the application package ultra-lightweight while providing full package management, `v1.3.0` adopts **Alpine Linux `aarch64` minirootfs**:
* **Compressed Archive Size**: **~3.5 MB** (using Zstandard `tar.zst`).
* **Unpacked Footprint**: **~12 MB** on internal storage.
* **Package Manager**: `apk` (musl libc-based, lightning-fast binary downloads and dependency resolution).
* **Available Packages**: Over 25,000 pre-compiled packages in the Alpine official repositories:
  * `nodejs`, `npm`, `yarn`, `pnpm`
  * `openjdk21-jdk`, `gradle`
  * `rust`, `cargo`
  * `php`, `composer`
  * `clang`, `gcc`, `g++`, `make`, `cmake`
  * `go`
  * `python3`, `py3-pip` with pre-compiled C-extensions

---

## 2. Hardened Zero-Trap Fast Path in `ld-musl-aarch64.so.1`

### Critical Mitigations Integrated (`ARGON-V13-10`, `ARGON-V13-11`):
1. **Dynamic Runtime Page Size**: In `arch/aarch64/bits/limits.h`, remove `#define PAGESIZE 4096` so `PAGE_SIZE` resolves dynamically to `libc.page_size` (`AT_PAGESZ = 16384` or `4096`).
2. **Safe Initial Base Address Reservation**: In `musl/ldso/dynlink.c: map_library()`, the initial reservation `mmap` passes `addr_min == 0` for PIE/shared libraries. We must **never** use `MAP_FIXED` when `addr_min == 0`.
3. **Correct Segment Offset Calculation**: Segments are read via `pread_full` using page-aligned `off_start` and segment length `(this_max - this_min)`.

```c
/* Step 1: In arch/aarch64/bits/limits.h, remove `#define PAGESIZE 4096`
 * so musl uses runtime `libc.page_size` (AT_PAGESZ) on both 4KB and 16KB kernels.
 */
static int pread_full(int fd, unsigned char *buf, size_t len, off_t off) {
    size_t total = 0;
    while (total < len) {
        ssize_t n = pread(fd, buf + total, len - total, off + total);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) break;
        total += (size_t)n;
    }
    return 0;
}

/* Step 2: Inside map_library() in ldso/dynlink.c: */
/* 2a. Initial address-space reservation across [addr_min, addr_max):
 *     NOTE: Do NOT use MAP_FIXED when addr_min == 0!
 */
map = mmap((void *)addr_min, map_len, PROT_READ | PROT_WRITE,
           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
if (map == MAP_FAILED) goto error;

/* Populate first segment and apply its initial protection */
if (pread_full(fd, map, this_max - this_min, off_start) < 0) goto error;
if (prot != (PROT_READ | PROT_WRITE) &&
    mprotect(map, this_max - this_min, prot) < 0) goto error;

/* 2b. Inside the subsequent PT_LOAD segment loop: */
for (; ph < (void *)((char *)eh + eh->e_phnum * eh->e_phentsize);
       ph = (void *)((char *)ph + eh->e_phentsize)) {
    if (ph->p_type != PT_LOAD) continue;
    ...
    /* Reuse or anonymously map [base + this_min, base + this_max) as RW */
    if (mmap(base + this_min, this_max - this_min, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS, -1, 0) == MAP_FAILED)
        goto error;
    if (pread_full(fd, base + this_min,
                   ((ph->p_vaddr + ph->p_filesz) - this_min), off_start) < 0)
        goto error;
    if (prot != (PROT_READ | PROT_WRITE) &&
        mprotect(base + this_min, this_max - this_min, prot) < 0)
        goto error;
}
```

---

## 3. Bootstrapper Initialization & State Preservation

`EnvironmentBootstrapper.kt` coordinates the cold-boot unpack and guarantees that user-installed packages survive APK upgrades:

```kotlin
// Inside EnvironmentBootstrapper.kt
val rootfsDir = File(filesDir, "rootfs")
val osRelease = File(rootfsDir, "etc/os-release")

if (!osRelease.exists()) {
    Log.i(TAG, "First boot: Extracting Alpine rootfs to ${rootfsDir.absolutePath}...")
    rootfsDir.mkdirs()
    extractZstdTarAsset(context, "rootfs.tar.zst", rootfsDir)
    
    // Seed default DNS and package repository mirrors
    File(rootfsDir, "etc/resolv.conf").writeText("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
    File(rootfsDir, "etc/apk/repositories").writeText(
        "https://dl-cdn.alpinelinux.org/alpine/v3.20/main\n" +
        "https://dl-cdn.alpinelinux.org/alpine/v3.20/community\n"
    )
} else {
    Log.i(TAG, "Alpine rootfs already initialized. Skipping extract to preserve user packages.")
}

// Reconcile and symlink proot tools and loader
createOrUpdateSymlink(File(binDir, "proot"), "$nativeDir/libproot.so")
createOrUpdateSymlink(File(binDir, "agy-sh"), "$nativeDir/libagy_sh.so")
createOrUpdateSymlink(File(usrLibDir, "libproot_loader.so"), "$nativeDir/libproot_loader.so")
```

### Persistence Invariant Across App Updates:
1. `InAppUpdateManager` downloads and installs updated APKs via Android's `PackageInstaller`.
2. The Android OS preserves `/data/data/ai.agy.app/files/rootfs/` intact.
3. Because `osRelease.exists()` is `true`, `EnvironmentBootstrapper` **skips unpacking**.
4. Every installed toolchain (`node`, `openjdk`, `cargo`, `rust`, `php`) and all user data in `/root/` or `/home/` remain **100% preserved**.

---

## 4. Bind Mounts & Hardware Networking Sync

When launching `proot`, the following critical host directories are bind-mounted into the rootfs:

| Host Path | Container Target | Purpose |
| :--- | :--- | :--- |
| `files/usr/etc/resolv.conf` | `/etc/resolv.conf` | Real-time dynamic DNS (Wi-Fi $\leftrightarrow$ Cellular updates) |
| `files/usr/tmp` | `/tmp` and `/dev/shm` | Shared memory and scratch file storage |
| `files/home` | `/data/data/ai.agy.app/files/home` | 1:1 host path preservation |
| `files/home/workspace` | `/root/workspace` | Default agent working directory |
| `files/home/.ssh` | `/root/.ssh` | Shared SSH credentials and known hosts |
| `/storage/emulated/0` | `/storage/emulated/0` | External user projects and SD card storage |
| `/dev` & `/dev/pts` | `/dev` & `/dev/pts` | Pseudo-terminals, `/dev/null`, `/dev/urandom` |
| `/proc` & `/sys` | `/proc` & `/sys` | Hardware telemetry and CPU/memory stats |
