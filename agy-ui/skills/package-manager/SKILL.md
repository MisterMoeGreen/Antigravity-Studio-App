---
name: package-manager
description: Install and manage runtime packages, language toolchains (Node.js, npm, Java, Rust, GCC, Python, PHP, Go), and system utilities inside the Alpine Linux subsystem on Android.
---

# Package Manager & Dynamic Toolchain Guide

Use this skill whenever the user or project requires installing, compiling, or executing runtime toolchains, libraries, or system packages (e.g., Node.js, npm, npx, Java OpenJDK, Rust/Cargo, GCC/Clang, Python, PHP, Go).

## Subsystem Architecture
Antigravity Studio on Android contains an integrated, W^X-aware Alpine Linux container subsystem (PRoot + Anonymous Memory ELF Loader) running at native ARM64 speed.
- The package manager is Alpine's **`apk`** (also aliased as **`pkg`**).
- Common tools (`apk`, `pkg`, `node`, `npm`, `npx`, `cargo`, `rustc`, `gcc`, `g++`, `clang`, `make`, `php`, `go`, `java`, `javac`) are pre-bridged directly into `$PATH`.
- All guest binaries and JIT engines (Node.js V8, OpenJDK HotSpot JVM) execute in anonymous memory without Android SELinux `W^X` blocks.

## Installing Packages
Install any package on demand via `run_command`:

```bash
apk add --no-cache <package-name...>
```
*(Or use `pkg install <package-name...>`)*

### Common Package Recipes
- **Node.js & npm / npx**:
  ```bash
  apk add --no-cache nodejs npm
  ```
- **C & C++ Compilers (GCC & Clang)**:
  ```bash
  apk add --no-cache gcc g++ musl-dev make cmake
  ```
- **Java (OpenJDK 21)**:
  ```bash
  apk add --no-cache openjdk21-jre-headless
  ```
- **Rust & Cargo**:
  ```bash
  apk add --no-cache rust cargo
  ```
- **Python 3 & Pip**:
  ```bash
  apk add --no-cache python3 py3-pip
  ```
- **PHP**:
  ```bash
  apk add --no-cache php php-cli
  ```
- **Git & Curl**:
  ```bash
  apk add --no-cache git curl
  ```
- **Go (Golang)**:
  ```bash
  apk add --no-cache go
  ```

## Running Commands & Installed Tools
Once a package is installed, invoke it directly from the shell or through `agy-sh`:
- **Direct command**: `node -v`, `npm test`, `npx <tool>`, `cargo build`, `gcc -o app app.c`, `java -version`
- **Container wrapper**: `agy-sh <cmd> [args...]` or `agy-sh -c "<cmd>"`

## Critical Constraints & Anti-Patterns (Avoid 4 Common Traps):
1. **NEVER create wrapper shell scripts in `/usr/bin/`**:
   - Android's private app storage is mounted `noexec`. Any attempt to execute a custom script with `#!/bin/sh` from `/usr/bin/` or `~` will fail with permission denied (`EACCES`).
   - The system already provides native binary bridge symlinks in `/usr/bin/` (`apk`, `pkg`, `node`, `npm`, `npx`, etc.) that pass kernel checks. Never attempt to manually create script wrappers there.
2. **NEVER claim Node.js or native binaries cannot be installed on Android**:
   - Antigravity Studio's anonymous-memory ELF loader executes arbitrary dynamic binaries and JIT runtimes at direct native speed. Proactively run `apk add --no-cache <pkg>` instead of declining.
3. **NEVER run `agy-sh` without arguments in background**:
   - Running bare `agy-sh` without arguments launches an interactive shell that expects terminal input and will block background tasks indefinitely. Always pass arguments: `agy-sh <command>` or `agy-sh -c "<command>"`.
4. **Shared Storage Scripts**:
   - When running scripts located on shared storage (`/storage/emulated/0/...`), invoke them through their interpreter (e.g., `node script.js`, `python3 script.py`, `bash script.sh`) because Android FUSE shared storage does not support POSIX `chmod +x`.
