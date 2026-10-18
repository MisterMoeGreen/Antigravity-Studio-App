# Specification 05: Toolchain Verification, Concurrency & Resource Guards

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Components**: Execution Verification Matrix & Resource Profiles  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Audit Status**: Hardened per Argon Review (`ARGON-V13-18`)

---

## 1. Toolchain Acceptance Verification Matrix

To verify that Antigravity Studio v1.3.0 achieves complete CLI runtime freedom under `targetSdkVersion 35`, the system must pass these six primary toolchain verification gates:

### Gate 1: Node.js & npm (V8 Dynamic JIT)
* **Installation**: `apk add nodejs npm`
* **Execution**: `node -e "console.log('V8 JIT Active: ' + (1+1))"`
* **Verification Criteria**:
  * Node loads V8 engine cleanly into anonymous memory.
  * Native C++ addons (e.g., `sqlite3`, `esbuild`) compile and load via `dlopen()` without triggering `-EACCES`.

### Gate 2: Java OpenJDK 21 & Gradle (HotSpot JVM)
* **Installation**: `apk add openjdk21-jdk gradle`
* **Execution**: `javac -version && java -version`
* **Verification Criteria**:
  * HotSpot JVM safepoint polling and signal handling run cleanly.
  * `JAVA_TOOL_OPTIONS` suppresses `/tmp/hsperfdata` shared memory locks and restricts GC thread pools to 4 active processors.

### Gate 3: Rust & Cargo (Compiler & Dynamic Execution)
* **Installation**: `apk add rust cargo`
* **Execution**: `cargo --version && rustc --version`
* **Verification Criteria**:
  * `rustc` compiles code to a native `aarch64` ELF binary in the workspace.
  * The newly compiled binary executes immediately without requiring APK packaging.

### Gate 4: PHP & Composer
* **Installation**: `apk add php composer`
* **Execution**: `php -r "echo 'PHP Native CLI: ' . phpversion();"`
* **Verification Criteria**:
  * Zend Engine executes CLI scripts and Composer downloads dependencies cleanly.

### Gate 5: C/C++ Build Toolchain (Clang / GCC / Make)
* **Installation**: `apk add build-base clang make cmake`
* **Execution**: Compile test program (`gcc test.c -o test && ./test`).
* **Verification Criteria**:
  * Compiler produces native 4 KB-aligned ELF binary.
  * Anonymous ELF loader loads and executes `./test` at native speed.

### Gate 6: Python 3 with Native C-Extensions
* **Installation**: `apk add python3 py3-pip py3-cryptography`
* **Execution**: `python3 -c "import cryptography; print('C-Extension OK')"`
* **Verification Criteria**:
  * Dynamic linker loads compiled `.so` C-extension from `rootfs/usr/lib/python3.*/` without SELinux violations.

---

## 2. Mobile Resource Concurrency Guardrails

Mobile smartphones operate under tight thermal and memory envelopes. Heavy multi-threaded compilation can trigger Android's Low Memory Killer (LMK) or phantom process limits.

Antigravity Studio enforces the following default environment limits inside the container:

### 1. CPU & Worker Constraints
* `CARGO_BUILD_JOBS=4`: Restricts Cargo to 4 parallel compilation threads.
* `MAKEFLAGS="-j4"`: Caps Make and CMake parallel jobs.
* `CMAKE_BUILD_PARALLEL_LEVEL=4`: Restricts CMake build concurrency.

### 2. Java / JVM Memory & Thread Ceiling (`ARGON-V13-18`)
* `JAVA_TOOL_OPTIONS="-XX:-UsePerfData -XX:ActiveProcessorCount=4 -Xmx1536m"`
  * `-XX:-UsePerfData`: Prevents JVM from creating shared mmap files in `/tmp/hsperfdata_<user>` that fail or deadlock under PRoot.
  * `-XX:ActiveProcessorCount=4`: Caps HotSpot GC and JIT compilation worker threads to 4, preventing ptrace contention across 8+ physical cores.
* `GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.workers.max=4 -Xmx1536m"`
  * Disables persistent Gradle daemons, saving 500+ MB RAM.

### 3. Node.js Memory Ceiling
* `NODE_OPTIONS="--max-old-space-size=1536"`: Restricts Node.js heap to 1.5 GB.
