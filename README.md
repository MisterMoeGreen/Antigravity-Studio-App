# Antigravity Studio for Android

<p align="center">
  <img src="agystudio.png" width="128" height="128" alt="Antigravity Studio Logo" />
</p>

<p align="center">
  <strong>The first fully standalone, autonomous AI coding agent & IDE for Android.</strong><br>
  Zero Termux required &bull; Zero root required &bull; Self-contained toolchain &bull; Android 10 to 17+ (API 28–35+)
</p>

<p align="center">
  <a href="https://github.com/MisterMoeGreen/Antigravity-Studio-App/releases/latest"><img src="badge-download.png" alt="Download APK" /></a>
</p>

---

## Overview

**Antigravity Studio** brings Google DeepMind's autonomous coding engine (**Antigravity**) directly to your Android device in a native, touch-first mobile IDE.

Download the lightweight APK, sign in with Google, and start building — no Termux, no root, no laptop required. The app ships everything you need: a full AI coding agent, built-in developer tools (bash, git, python, ssh), and the ability to install virtually any programming language or compiler on the fly.

What makes it unique: Antigravity Studio is the first app to run real native compilers and JIT runtimes (Node.js, Java, Rust, GCC, Clang, Go) **directly on Android at full native speed** — something normally blocked by Android's security restrictions. It does this transparently, so you just write code and the agent handles the rest.

### Screenshots

<p align="center">
  <a href="docs/screenshots/01-agent-streaming.png"><img src="docs/screenshots/01-agent-streaming.png" width="170" alt="Agent Chat & Live Thinking" title="Agent Chat & Live Thinking" /></a>
  <a href="docs/screenshots/02-tool-calls.png"><img src="docs/screenshots/02-tool-calls.png" width="170" alt="Tool Call Inspection" title="Tool Call Inspection" /></a>
  <a href="docs/screenshots/03-terminal-mode.png"><img src="docs/screenshots/03-terminal-mode.png" width="170" alt="Integrated Terminal Mode" title="Integrated Terminal Mode" /></a>
  <a href="docs/screenshots/04-model-reasoning.png"><img src="docs/screenshots/04-model-reasoning.png" width="170" alt="Model & Reasoning Effort Selector" title="Model & Reasoning Effort Selector" /></a>
  <a href="docs/screenshots/05-navigation-drawer.png"><img src="docs/screenshots/05-navigation-drawer.png" width="170" alt="Navigation Drawer & Sessions" title="Navigation Drawer & Sessions" /></a>
  <a href="docs/screenshots/06-workspace-picker.png"><img src="docs/screenshots/06-workspace-picker.png" width="170" alt="Session Workspace Picker" title="Session Workspace Picker" /></a>
  <a href="docs/screenshots/07-settings-hub.png"><img src="docs/screenshots/07-settings-hub.png" width="170" alt="Settings & Hub" title="Settings & Hub" /></a>
  <a href="docs/screenshots/08-package-manager.png"><img src="docs/screenshots/08-package-manager.png" width="170" alt="Built-in Package Manager" title="Built-in Package Manager" /></a>
</p>
<p align="center"><sub>Click any screenshot to view full size.</sub></p>

---

## Key Features

### 🚀 Code in Any Language — Directly on Android
Install and run virtually any programming language or compiler with a single command. Node.js, Java, Python, C/C++, Rust, Go, Clang, PHP — all execute at **full native ARM64 speed**, not through emulation. The agent can install toolchains on demand, or you can install them yourself from the Settings Hub.

### 📦 Built-In Package Manager
Access thousands of packages from Alpine Linux's repository, installable in seconds:
* **From the Settings Hub**: Tap **Settings ⚙ → Packages** to browse installed packages, check disk usage, search for new ones, or use quick-install presets (Node.js, Python, Rust, Java, C/C++, Go, etc.).
* **From the Chat**: Just ask the agent — *"Install Node.js"* or *"I need OpenCV"* — and it finds the right packages, resolves dependencies, and installs them.
* **From Commands**: The agent runs `apk add` directly when it needs a tool, no manual intervention required.

### 💬 Interactive Touch Prompts
When the AI agent needs your input — choosing an architecture, picking between options, or confirming a decision — it presents **native touch dialogs** instead of raw terminal prompts:
* Single-select radio cards and multi-select checkboxes
* Free-form text write-in for custom answers
* Clean bottom-sheet modals designed for mobile screens

### 🧰 Pre-Bundled Developer Toolchain
Everything an AI coding agent needs ships inside the APK — no separate installs:
* **bash 5.3** & **busybox 1.38** — Shell and core UNIX utilities
* **python 3.14** — Full runtime with 80+ compiled extensions
* **git 2.56** — Clone, commit, and push over HTTPS or SSH
* **ripgrep 15.2** — Fast codebase search
* **curl 8.22** — Network requests and API calls
* **ssh 9.9** — Secure remote connections and Git operations

### ⚙️ Settings Hub
A touch-first control panel for managing your development environment:
* **API Keys** — Store service tokens (GitHub, OpenAI, Tavily, etc.) that auto-inject into the agent
* **SSH Keys** — Generate ED25519 or RSA-4096 keys and copy the public key with one tap
* **MCP Servers** — Connect Model Context Protocol servers (local stdio or remote HTTP)
* **Skills & Custom Instructions** — Browse, create, and edit agent behaviors and workspace rules
* **Packages** — Visual package manager for the built-in Alpine subsystem
* **Encrypted Backup & Restore** — Password-protected configuration export/import (AES-256-GCM + scrypt) to migrate keys, skills, MCPs, and packages across devices

### ⚡ Persistent Agent Client Protocol (ACP) Daemon
Powered by Google's native **Agent Client Protocol** (`agy acp`), Antigravity Studio operates a long-lived, high-speed JSON-RPC 2.0 daemon:
* **Zero Turn Cold-Start Latency**: In-memory token cache reuse across conversation turns eliminates process restart delays.
* **Real-Time Streaming**: Live streaming of agent thoughts (`agent_thought`), tokens (`agent_token`), and tool executions (`tool_call`).
* **Non-Destructive Turn Interruption**: Stop agent turns cleanly via `session/cancel` notifications without terminating engine processes.
* **Background Task Telemetry**: Dedicated mobile status bar and bottom-sheet manager to track and selectively stop active background jobs.

### 🔋 Runs in the Background
Lock your screen or switch apps — the agent keeps working:
* Foreground service with persistent notification keeps long-running tasks alive
* Automatic network handling when switching between Wi-Fi and cellular

### 🔄 Dual-Track Software Updates
* **Antigravity Studio App**: Check for APK releases, inspect changelogs, and install in-place updates.
* **Antigravity CLI Engine**: Query official Google engine releases, update independently of the app, and repair or reinstall the engine on demand from Settings.

---

## Architecture & How It Works

```mermaid
flowchart TD
    subgraph UI ["Android App Shell (Kotlin)"]
        Activity["MainActivity (WebView + Chrome Custom Tabs)"]
        Service["AgyForegroundService (SpecialUse FGS + WakeLock)"]
        Bootstrapper["EnvironmentBootstrapper (Zstd Tar Extractor)"]
        Updater["InAppUpdateManager (PackageInstaller API)"]
    end

    subgraph NativeLibs ["Virtualization & Native Libs (/data/app/.../lib/arm64)"]
        AgyLauncher["libagy_launcher.so (Native Linker Bridge - 49KB)"]
        PRoot["libproot.so (W^X-Aware Tracer Engine)"]
        AnonLoader["libproot_loader.so (Anonymous ELF Loader)"]
        Bridge["libagy_sh.so (Multicall Bridge: apk, node, gcc...)"]
        Shim["libagy_shim.so (LD_PRELOAD Shebang Virtualizer)"]
        AskLauncher["libask_question.so (Touch Decision Launcher)"]
        GlibcLd["libglibc_ld.so (Glibc Dynamic Linker)"]
        HostTools["libbash.so, libpython3_bin.so, libgit.so, librg.so, libcurl_bin.so"]
    end

    subgraph Sandbox ["App Sandbox Storage (/data/data/ai.agy.app/files)"]
        UsrBin["usr/bin/* (Symlink Farm: agy, agy-sh, apk, node, gcc...)"]
        AgyEngine["usr/opt/agy/agy (Unbundled CLI Engine)"]
        UsrRootfs["usr/rootfs/ (Alpine Linux Minirootfs v3.20.9)"]
        Telemetry["usr/tmp/android_telemetry.json"]
        Resolv["usr/etc/resolv.conf (Dynamic DNS)"]
        ServerPy["usr/share/agy-ui/server.py (Async Backend)"]
        AskBridge["usr/share/agy-ui/ask_question.py"]
        WebAssets["usr/share/agy-ui/index.html (SPA UI + Modals)"]
    end

    Activity <-->|"HTTP / WebSocket on 127.0.0.1:8081"| ServerPy
    Service -->|"Spawns with LD_PRELOAD"| ServerPy
    ServerPy <-->|"Persistent agy acp (JSON-RPC 2.0 Stdio)"| UsrBin
    UsrBin -->|"agy"| AgyLauncher
    AgyLauncher -->|"Dispatches via libglibc_ld.so"| AgyEngine
    AgyEngine -->|"run_command"| Shim
    Shim -->|"Redirects to"| UsrBin

    UsrBin -->|"Host Tools"| HostTools
    UsrBin -->|"Multicall Bridge"| Bridge
    Bridge -->|"Spawns via"| PRoot
    PRoot -->|"Loads Binaries via"| AnonLoader
    AnonLoader -->|"Executes in Container"| UsrRootfs

    AgyEngine -->|"Invokes Decision"| AskLauncher
    AskLauncher -->|"Executes"| AskBridge
    AskBridge -->|"Dispatches JSON"| ServerPy
    ServerPy -->|"Pushes Bottom Sheet"| WebAssets
    WebAssets -->|"Touch Feedback Response"| ServerPy
    ServerPy -->|"Returns Answer"| AskBridge

    Bootstrapper -->|"Unpacks usr-data.tar.zst & rootfs.tar.zst"| Sandbox
```

### Under the Hood

Running real compilers, JIT runtimes, and persistent AI agents on Android is hard — the OS blocks dynamic code execution by design and Android's low-memory killer aggressively reclaims resources. Antigravity Studio solves this with four core components:

* **Persistent Agent Client Protocol (ACP)** (`agy acp`): Rather than re-spawning batch CLI processes (`agy -p`) on every turn, the backend orchestrates a long-running `agy acp` daemon communicating over bidirectional JSON-RPC 2.0 stdio streams. This preserves in-memory token cache reuse across conversation turns, streams agent thoughts and tool calls in real time, enables non-destructive turn cancellation (`session/cancel`), and provides background task telemetry without process thrashing.
* **Anonymous ELF Loader** (`libproot_loader.so`): Loads binaries into anonymous memory pages that bypass Android's SELinux execution barriers entirely.
* **W^X-Aware PRoot Engine** (`libproot.so`): A user-space virtualization layer that intercepts system calls via `ptrace`, translating paths and enforcing safe execution without kernel modifications.
* **Bionic Shim** (`libagy_shim.so`): An `LD_PRELOAD` hook that rewrites shebangs (`#!/bin/sh`, `#!/usr/bin/env python3`) to point to the bundled toolchain, since Android lacks standard Linux paths.

The app also embeds a lightweight Alpine Linux rootfs that provides the `apk` package manager and its full repository — this is how `apk add nodejs` or `apk add gcc` works on a phone. All 115+ native binaries are 16 KB page-aligned and signed for Android 10–17+ compatibility.

### Cold Boot Workflow
1. **Runtime Extraction**: On first launch (or build update), `EnvironmentBootstrapper` extracts:
   * `usr-data.tar.zst`: Python stdlib, git templates, SSL certificates, Web UI, `ask_question.py`, and built-in skills.
   * `rootfs.tar.zst`: Alpine Linux `aarch64` minirootfs non-destructively unpacked into `usr/rootfs/`.
2. **Symlink Farm Assembly**: Creates symlinks in `usr/bin/` pointing to the native binaries in `/data/app/.../lib/arm64/`, including `libagy_launcher.so` (native bridge for on-demand engine dispatch), host toolchain utilities, and multicall bridges (`agy-sh`, `apk`, `pkg`, `node`, `npm`, `npx`, `cargo`, `gcc`, `java`, `ask_question`).
3. **Daemon Initialization**: `AgyForegroundService` acquires a `WakeLock`, selects an available port (`8081` $\to$ `8082` $\to$ `8090` $\to$ ephemeral), and starts `server.py` with `LD_PRELOAD=libagy_shim.so`.
4. **UI Readiness**: `MainActivity` polls `/health` until the backend signals ready, then renders the responsive mobile IDE interface in WebView.

---

## Getting Started

### Installation
1. Download **`AntigravityStudio.apk`** from the [GitHub Releases](https://github.com/MisterMoeGreen/Antigravity-Studio-App/releases) page.
2. Open your device file manager (Files, Solid Explorer, etc.) and tap the downloaded `.apk`.
3. If prompted, allow your browser or file manager to **Install unknown apps**.
4. Tap **Install** and launch **Antigravity Studio**.

### First Launch & Setup
1. On initial startup, grant the **Notification Permission** (required for background execution).
2. Allow a few seconds for the environment and Alpine rootfs to initialize.
3. **Engine Setup**: When the Antigravity Engine Setup prompt appears, tap **Download & Setup Engine**. The app downloads the official CLI binary directly from Google DeepMind and applies the on-device Android compatibility patch.
4. **Sign In**: Tap **Sign in with Google**. A browser tab will open for authentication. Copy the authorization code, paste it into the prompt, and hit send.
5. Select your workspace directory and start coding!

---

## Project Structure

```text
AgyStudio/
├── agystudio.png                         # Application logo
├── app/
│   ├── src/main/
│   │   ├── AndroidManifest.xml           # Permissions, FGS declaration, FileProvider
│   │   ├── cpp/
│   │   │   ├── agy_launcher.c            # Native C linker bridge for on-demand zero-mutation engine dispatch
│   │   │   ├── agy_shim.c                # Bionic C virtualization shim (execve / shebang parser)
│   │   │   ├── agy_sh.c                  # Native multicall toolchain bridge (apk, node, gcc...)
│   │   │   ├── ask_question_launcher.c   # Native ELF launcher for interactive touch modals
│   │   │   ├── proot/                    # W^X-aware PRoot tracer core (talloc, seccomp BPF)
│   │   │   ├── proot_loader/             # Freestanding anonymous ELF loader (mmap/pread64/mprotect)
│   │   │   └── CMakeLists.txt            # Native build script
│   │   ├── java/ai/agy/app/
│   │   │   ├── MainActivity.kt           # WebView container, back-button handling & OAuth
│   │   │   ├── AgyForegroundService.kt   # Foreground service, WakeLock, Dynamic DNS & telemetry
│   │   │   ├── EnvironmentBootstrapper.kt # Cold-boot unpack, rootfs extraction & symlink farm
│   │   │   ├── InAppUpdateManager.kt     # Tier-1 OTA asset updater & Tier-2 APK installer
│   │   │   └── UpdateReceiver.kt         # PackageInstaller broadcast receiver
│   │   ├── assets/
│   │   │   ├── usr-data.tar.zst          # Compressed runtime assets (Python stdlib, web UI, skills)
│   │   │   └── rootfs.tar.zst            # Alpine Linux aarch64 minirootfs (jemalloc, patched musl)
│   │   └── res/                          # Vector launcher icons, styles, and XML configs
├── agy-ui/                               # Web frontend & async Python server source
│   ├── index.html                        # Mobile SPA chat, Settings Hub & Package Manager UI
│   ├── server.py                         # Async process orchestrator, package API & WebSocket dispatcher
│   ├── ask_question.py                   # CLI-to-UI interactive modal bridge
│   ├── default_app_prompt.md             # Mobile system directives, UI navigation & container rules
│   ├── skills/                           # Built-in agent skills
│   │   └── package-manager/SKILL.md      # Auto-seeded Alpine package recipes and execution directives
│   ├── manifest.json                     # PWA standalone application manifest
│   └── sw.js                             # Service Worker offline asset cache
├── scripts/
│   ├── verify_or_patch_libagy.sh         # Compiles libagy_launcher.so and enforces engine unbundling
│   ├── sync_web_assets.sh                # Synchronizes web UI assets into usr-data.tar.zst
│   └── verify_architectural_invariants.py # Automated pre-build AST and API boundary integrity gate
├── specs/
│   ├── initial/                          # v1.0.0 Architecture & Core Toolchain specifications
│   ├── 1.1.0/                            # v1.1.0 System Prompts, Dynamic DNS & Telemetry specs
│   ├── v1.2.0/                           # v1.2.0 Interactive Touch Questions & Decision Modals specs
│   ├── v1.3.0/                           # v1.3.0 W^X PRoot Subsystem, Anonymous Loader & Package UI specs
│   ├── 1.8.0/                            # v1.8.0 Persistent ACP Daemon, Event Streaming & Telemetry specs
│   ├── OVERVIEW_AND_IMPLEMENTATION_GUIDE.md # Master verification gates & acceptance tests
│   └── IMPLEMENTATION_LOG.md             # Execution audit log across all phases
├── build.gradle.kts                      # Root project Gradle configuration
└── settings.gradle.kts                   # Module settings
```

---

## Building from Source

To compile the APK from source on an Android device (via Termux) or on a Linux desktop:

```bash
# 1. Clone the repository
git clone git@github.com:MisterMoeGreen/Antigravity-Studio-App.git
cd Antigravity-Studio-App

# 2. Verify native launcher bridge and sync web assets
bash scripts/verify_or_patch_libagy.sh
bash scripts/sync_web_assets.sh

# 3. Build signed Release APK
bash ./gradlew assembleRelease

# 4. Built APK will be available at:
# app/build/outputs/apk/release/app-release.apk
```

---

## Upstream Lineage & Attribution

* **Google Antigravity CLI**: Antigravity Studio interfaces with the official Linux ARM64 binary of Google's Antigravity CLI (`agy`), compiled from Google's monorepo (`google3`). All model communication, agent reasoning, and authentication route authentically through official Google channels.
* **Kernel VA39 Compatibility Research ([wallentx/antigravity-cli-termux](https://github.com/wallentx/antigravity-cli-termux))**: Gratefully recognized for discovering and pioneering the 39-bit Virtual Address (VA39) memory patch over Google's TCMalloc runtime, making standard Linux ARM64 binaries compatible with Android kernels. Antigravity Studio ingests the official Linux ARM64 engine directly from Google DeepMind's Cloud CDN and applies this VA39 patch autonomously on-device.

---

## License & Credits

* **Application Code**: Licensed under the Apache License 2.0.
* **Third-Party Components & Licenses**: Bundled toolchain utilities (`bash`, `git`, `python`, `ripgrep`, `curl`, `busybox`, `glibc`, `proot`, `alpine-linux`, `musl`) retain their respective open-source licenses.
* **Vector Iconography**: Provided by [Font Awesome Free](https://fontawesome.com) under the [CC BY 4.0 License](https://creativecommons.org/licenses/by/4.0/).
* **Full Attributions & Disclaimers**: See [CREDITS.md](CREDITS.md) for complete license notices, upstream acknowledgments, and trademark disclaimers.
