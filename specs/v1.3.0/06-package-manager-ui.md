# Specification 06: On-Device Package Manager UI & Interactive Install Handoff

> **Release Target**: `v1.3.0` (Native W^X-Aware PRoot Subsystem)  
> **Components**: Settings Hub Package UI, Fast Alpine DB Parser, and AI Disambiguation Bridge  
> **Target SDK**: `35` (Android 15 / 16 / 17+) | **Architecture**: `arm64-v8a`  
> **Integration Baseline**: Built upon v1.2.0 Touch Modals & v1.3.0 W^X PRoot Engine  

---

## 1. Overview & Problem Statement

In `v1.3.0`, Antigravity Studio introduced the **W^X-Aware PRoot Subsystem and Alpine Linux Minirootfs**, granting the agent unconstrained on-device capability to install arbitrary compilers, runtimes, and tools via `apk add`.

However, the user experience currently lacks a visual management interface for these packages:
1. **Zero Visibility**: Users cannot see which packages are currently installed in the container, how much internal storage they consume, or what versions are active without asking the agent in chat.
2. **No Manual Lifecycle Controls**: Removing or updating packages requires launching a conversational agent turn rather than providing direct, one-tap UI actions.
3. **Vague Intent Friction**: When a user wants a toolchain (e.g., "I need C++" or "Install Python"), command-line package names are often nuanced (`g++`, `build-base`, `clang`, `python3`, `py3-pip`). The system needs a guided installation flow that uses the v1.2.0 interactive question bridge to clarify intent before running `apk add`.

**Specification 06 defines the On-Device Package Manager UI**:
* A new **Package Manager** sub-view in the Settings Hub.
* Fast, zero-overhead direct parsing of Alpine's database (`/etc/apk/world` and `/lib/apk/db/installed`) in `< 3ms`.
* Per-package cards displaying name, version, description, and size in MB, with direct **Delete** and **Update** actions.
* Top action controls to **Update All Packages** and **Install New Package**.
* An **Install Package** touch sheet modal linked to an automated AI turn handoff that leverages `ask_question` for intelligent package candidate disambiguation.

---

## 2. Architecture & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Mobile User
    participant Hub as Settings Hub (index.html)
    participant Srv as server.py (Async Daemon)
    participant Rootfs as Container Storage (/files/rootfs)
    participant Agy as Antigravity Engine (libagy.so)
    participant Bridge as ask_question Bridge

    User->>Hub: Taps "Package Manager" in Settings
    Hub->>Srv: WebSocket: {"type": "get_packages"}
    Srv->>Rootfs: Direct read of /etc/apk/world & /lib/apk/db/installed (< 3ms)
    Srv-->>Hub: {"type": "packages_data", "packages": [...], "total_size_mb": 62.4}
    Hub->>Hub: Renders package cards with version, size & actions

    alt User Taps Delete or Update
        User->>Hub: Taps [Delete] (with confirm) or [Update]
        Hub->>Srv: {"type": "delete_package", "name": "nodejs"}
        Srv->>Rootfs: Executes "usr/bin/agy-sh -c 'apk del --purge nodejs'"
        Srv-->>Hub: Broadcasts updated package list & success toast
    else User Taps "Install Package"
        User->>Hub: Taps [+ Install Package] -> Enters "c++" -> Taps [Submit]
        Hub->>Hub: Closes modal & Settings Hub -> Switches to Main Chat
        Hub->>Srv: {"type": "install_package_prompt", "package_query": "c++"}
        Srv->>Agy: Spawns new session with package installation prompt
        Note over Agy: Agy searches "apk search c++" -> Multiple matches found
        Agy->>Bridge: ask_question '{"questions": [{"question": "Which C++ toolchain?", "options": ["(Recommended) build-base (GCC + G++)", "clang", "g++"]}]}'
        Bridge-->>Hub: Slides up #modalAskQuestion touch sheet
        User->>Hub: Selects "(Recommended) build-base" -> Taps [Submit]
        Hub-->>Bridge: Resolves question future
        Bridge-->>Agy: Returns "A1: (Recommended) build-base"
        Agy->>Rootfs: Executes "apk add --no-cache build-base"
        Agy-->>Hub: Streams confirmation & compiler verification to chat
    end
```

---

## 3. UI Wireframes & Mobile Ergonomics

### A. Settings Hub Menu Item
In `#settingsHubMenuList`, a dedicated navigation card positioned after "MCP Servers":
* **Icon**: Font Awesome Free `box` SVG.
* **Title**: `Package Manager`.
* **Subtitle**: `Alpine Linux container packages & runtimes`.
* **Right Accessory**: Live count badge (e.g. `2 pkgs` or `62 MB`) + chevron `›`.

---

### B. Package Manager Sub-View Layout (`#viewPackages`)

```
┌────────────────────────────────────────────────────────┐
│ [← Back]              Package Manager         [+ Add]  │  <-- 56px Sticky Header
├────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────┐ │
│ │  Container Storage: 62.4 MB  •  2 Packages         │ │  <-- Storage Summary Card
│ └────────────────────────────────────────────────────┘ │
│                                                        │
│ ┌───────────────────────┐    ┌───────────────────────┐ │
│ │  [⬆️ Update All]      │    │  [+ Install Package]  │ │  <-- Top Action Bar
│ └───────────────────────┘    └───────────────────────┘ │
│                                                        │
│ USER INSTALLED PACKAGES                                │
│ ┌────────────────────────────────────────────────────┐ │
│ │  nodejs                          v20.15.1-r0 • 42MB│ │
│ │  JavaScript runtime built on Google V8 engine       │ │
│ │  ────────────────────────────────────────────────  │ │
│ │  [🔄 Update]                           [🗑️ Delete] │ │  <-- Package Card
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │  npm                               v10.8.2-r0 • 20MB│ │
│ │  Package manager for the JavaScript programming ...│ │
│ │  ────────────────────────────────────────────────  │ │
│ │  [🔄 Update]                           [🗑️ Delete] │ │
│ └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

1. **Header Actions**:
   * Left: `[← Back]` returns to Settings Hub root menu.
   * Right: `[+ Add]` triggers the Install Package sheet.
2. **Top Action Bar**:
   * **`[⬆️ Update All]` (`#btnUpdateAllPackages`)**: Runs `apk update && apk upgrade` across all packages with a live progress indicator.
   * **`[+ Install Package]` (`#btnOpenInstallPackageModal`)**: Opens the package installation modal.
3. **Package Cards**:
   * **Header Row**: Package name (bold 15px) + Version badge (`v20.15.1-r0`) + Size badge (`42.4 MB`).
   * **Description**: Clamped to 2 lines of text from package metadata.
   * **Action Buttons**:
     * `[🔄 Update]`: Upgrades the single package (`apk add --upgrade <name>`).
     * `[🗑️ Delete]`: Prompts a confirmation bottom sheet, then removes the package (`apk del --purge <name>`).
4. **Touch Ergonomics**:
   * Minimum touch target $\ge 44\text{px}$ on all buttons.
   * Native slide transitions matching existing Settings Hub views.

---

### C. Install Package Modal Sheet (`#modalInstallPackage`)

```
┌────────────────────────────────────────────────────────┐
│ Install Package                                [✕]    │
├────────────────────────────────────────────────────────┤
│ What package do you want to install?                   │
│ ┌────────────────────────────────────────────────────┐ │
│ │ e.g. nodejs, rust, openjdk21, ffmpeg, clang...     │ │
│ └────────────────────────────────────────────────────┘ │
│                                                        │
│ Quick Presets:                                         │
│ [Node.js]  [Python 3]  [OpenJDK 21]  [Rust]  [GCC]     │
│ [Go]       [Clang]     [PHP]         [FFmpeg]          │
│                                                        │
│ ┌───────────────────────┐    ┌───────────────────────┐ │
│ │ [ Cancel ]            │    │ [ Install Package ]   │ │
│ └───────────────────────┘    └───────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

* **Prompt Label**: *"What package do you want to install?"*
* **Text Input (`#inputPackageName`)**: Focused automatically with touch keyboard support.
* **Preset Chips**: One-tap quick selection for practical developer toolchains:
  - `Node.js` (`nodejs npm`)
  - `Python 3` (`python3 py3-pip`)
  - `OpenJDK 21` (`openjdk21-jre-headless`)
  - `Rust` (`rust cargo`)
  - `C/C++ (GCC)` (`build-base`)
  - `Go` (`go`)
  - `Clang` (`clang lld musl-dev`)
  - `PHP` (`php83 php83-cli`)
  - `FFmpeg` (`ffmpeg`)
* **Empty State UI**: Displays an empty state placeholder when `packages.length === 0`: *"No additional packages installed. Tap [+ Install Package] to add compilers and runtimes."*
* **Mobile Safe Area**: Applies `padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px))` across `#viewPackages` and `#modalInstallPackage`.
* **Submit Action (`#btnConfirmInstallPackage`)**: Initiates the AI session handoff.

---

## 4. Backend Engine & Zero-Overhead Package Reader

### A. High-Speed DB Parser (`server.py`)
To ensure the Settings Hub loads instantaneously without waking the PRoot tracer or incurring process execution latency:
* **Dynamic Rootfs Resolution**: Resolve rootfs path via `os.environ.get("AGY_FILES_DIR")` or relative to `server.py` (`Path(__file__).resolve().parent.parent / "rootfs"`). Gracefully return `{"packages": [], "total_size_mb": 0.0}` if the container rootfs is not yet initialized.
* **Protected Core Packages**: An immutable set of essential system packages filtered from UI listing and permanently forbidden from deletion:
  ```python
  PROTECTED_PACKAGES: frozenset[str] = frozenset({
      "alpine-baselayout",
      "alpine-baselayout-data",
      "alpine-keys",
      "alpine-release",
      "apk-tools",
      "busybox",
      "busybox-binsh",
      "ca-certificates-bundle",
      "libc-utils",
      "libcrypto3",
      "libssl3",
      "musl",
      "musl-utils",
      "scanelf",
      "ssl_client",
      "zlib",
  })
  ```
* **Explicit Packages**: Read `<rootfs>/etc/apk/world`, stripping version pins and repo tags via `re.split(r"[@=<>~]", line.strip(), maxsplit=1)[0]` and filtering out `PROTECTED_PACKAGES`.
* **Package Metadata Index**: Single-pass parse of `<rootfs>/lib/apk/db/installed`:
  - `P:<name>`: Package name.
  - `V:<version>`: Installed version string.
  - `I:<bytes>`: Installed size in bytes.
  - `T:<description>`: Short package description.
  - `U:<url>`: Upstream URL.
  - `D:<dependencies>`: Space-separated dependencies (stripped of constraints and virtual `so:`, `cmd:`, `pc:`, `!` tags).
* **Storage Accounting & Meta-Package Rollup**:
  - `total_size_mb`: Sums `I:<bytes>` across **all** non-protected packages in `/lib/apk/db/installed` (including transitive dependencies like `gcc`, `libstdc++`, `llvm-libs`), so `Container Storage` reflects true disk consumption.
  - For meta-packages (where `I:<bytes> <= 16384` and `deps` are present, such as `build-base`), roll up the sum of direct non-base dependencies' installed sizes so the UI card displays meaningful size information instead of `0.0 MB`.
* **Performance**: Direct Python file I/O completes in **$< 3\text{ms}$** on ARM64 flash storage.

---

### B. Mutation Execution Engine & Security Controls
All package mutations run asynchronously inside the container via direct subprocess execution, guarded against injection, race conditions, and bricking:

1. **Package Name Validation**: All input package names are strictly validated against `^[a-zA-Z0-9][a-zA-Z0-9._+-]{0,127}$`:
   ```python
   APK_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+-]{0,127}$")
   def validate_pkg_name(name: str) -> str:
       if not isinstance(name, str) or not APK_PKG_NAME_RE.match(name):
           raise ValueError(f"Invalid Alpine package name: {name!r}")
       return name
   ```
2. **Deletion Protection**: Attempting to delete any package in `PROTECTED_PACKAGES` raises a `PermissionError` immediately before invoking `apk`.
3. **Lock Serialization & Timeout**:
   - In-process operations are serialized via a global `APK_MUTATION_LOCK = asyncio.Lock()`.
   - Cross-process concurrency (e.g. agent CLI turns running `apk` concurrently) is handled by passing `--wait 30` to every `apk` invocation.
   - Operations are bounded by `asyncio.wait_for(..., timeout=180.0)`.
4. **Direct Subprocess Execution (No Shell Injection)**: Subprocesses are invoked directly with `asyncio.create_subprocess_exec` using `argv` arrays (never via shell interpolation or `agy-sh -c`):

| Action | Executed Subprocess (`argv` array) |
| :--- | :--- |
| **Delete Package** | `[apk_bin, "--wait", "30", "del", "--purge", "--", pkg_name]` |
| **Upgrade Package** | `[apk_bin, "--wait", "30", "add", "--upgrade", "--", pkg_name]` |
| **Upgrade All** | `[apk_bin, "--wait", "30", "upgrade", "--update-cache"]` |

5. **Ghost Deletion Detection**: If `apk del --purge -- <name>` exits with code `0` but prints `"not removed due to"`, or if `<name>` remains present in `/lib/apk/db/installed` because another installed package depends on it, `server.py` re-adds `<name>` to `/etc/apk/world` and returns an error informing the user which dependent package must be removed first.

---

### C. WebSocket Protocol Frame Contracts

```json
// 1. Client Requests Package List
{"type": "get_packages"}

// 2. Server Returns Installed Packages
{
  "type": "packages_data",
  "packages": [
    {
      "name": "nodejs",
      "version": "20.15.1-r0",
      "size_mb": 42.5,
      "description": "JavaScript runtime built on Google V8 engine",
      "url": "https://nodejs.org/"
    }
  ],
  "total_size_mb": 42.5
}

// 3. Client Requests Package Deletion
{"type": "delete_package", "name": "nodejs"}

// 4. Client Requests Single Upgrade
{"type": "upgrade_package", "name": "nodejs"}

// 5. Client Requests Upgrade All
{"type": "upgrade_all_packages"}

// 6. Client Initiates Package Install Handoff
{"type": "install_package_prompt", "package_query": "c++"}

// 7. Server Broadcasts Mutation Status (In-Progress)
{
  "type": "package_mutation_status",
  "action": "delete" | "upgrade" | "upgrade_all",
  "package": "nodejs",
  "in_progress": true
}

// 8. Server Broadcasts Mutation Completion / Result
{
  "type": "package_mutation_result",
  "action": "delete" | "upgrade" | "upgrade_all",
  "package": "nodejs",
  "success": true,
  "message": "Removed nodejs (42.5 MB freed)"
}
```

---

## 5. Agent Disambiguation Protocol (v1.2.0 Synergy)

When the user submits a package query via the install modal:
1. `server.py` sanitizes `package_query` (`re.sub(r"[\r\n\t`]", " ", query).strip()[:100]`).
2. Checks whether an agent turn is currently active. If busy, returns an error toast rather than clobbering active state.
3. Dispatches a visible user message and structured system prompt without silently wiping conversation context:

```text
The user wants to install the package: "{package_query}" in the Antigravity Studio Alpine Linux container.

Environment Directives:
- Subsystem: Alpine Linux aarch64 (PRoot container with W^X anonymous ELF loader).
- Package Manager: `apk` (uses `apk add --no-cache <package>`).
- Search Tool: `apk search <term>`.

Instructions:
1. Exact Match Check:
   If "{package_query}" unambiguously identifies a valid Alpine package (e.g., `nodejs`, `python3`, `rust`, `git`, `ffmpeg`, `go`):
   - Immediately execute `apk add --no-cache {package_query}` via `run_command`.
   - Run a quick smoke verification (e.g. `<binary> --version`).
   - Confirm successful installation and installed version to the user.

2. Ambiguous Query Disambiguation (CRITICAL):
   If "{package_query}" is ambiguous, a general category, or could correspond to multiple package candidates (e.g., "c++", "postgres", "java", "opencv", "linter", or a keyword search):
   - First, run `apk search <term>` via `run_command` to discover valid candidate packages.
   - Present the candidate packages to the user using the interactive question tool:
     ask_question << 'EOF'
     {
       "questions": [
         {
           "question": "Which package would you like to install?",
           "options": [
             "(Recommended) <package_1> - <brief description>",
             "<package_2> - <brief description>",
             "<package_3> - <brief description>"
           ],
           "is_multi_select": false
         }
       ],
       "toolAction": "Selecting package to install",
       "toolSummary": "Package disambiguation"
     }
     EOF
   - CRITICAL OPTION EXTRACTION: When the user selects an option from `ask_question`, extract ONLY the valid Alpine package name(s) (stripping prefixes like `(Recommended)` and suffixes like `- <description>`) before executing `apk add --no-cache <pkg>`.
   - Run a quick verification of the installed binary to confirm readiness.
```

---

## 6. Acceptance Criteria & Verification Checklist

- [ ] **AC-6.1: Fast Alpine Package DB Parser & API Endpoint**
  - Verify `server.py` implements `get_installed_packages()` by directly reading `/etc/apk/world` and `/lib/apk/db/installed`, correctly calculating size in MB, rolling up meta-packages, and filtering protected base packages.
  - **Verification Gate**:
    ```bash
    python3 -c '
    import sys; sys.path.insert(0, "agy-ui")
    from server import get_installed_packages
    res = get_installed_packages()
    assert "packages" in res and "total_size_mb" in res
    assert isinstance(res["packages"], list)
    print("AC-6.1 PASSED: Package parser returned", len(res["packages"]), "packages")
    '
    ```

- [ ] **AC-6.2: Package Mutation Handlers (`delete`, `upgrade`, `upgrade_all`)**
  - Verify WebSocket message routing for `delete_package`, `upgrade_package`, and `upgrade_all_packages` executes `apk` directly with `asyncio.create_subprocess_exec` using `--wait 30`.
  - **Verification Gate**:
    ```bash
    grep -q 'delete_package' agy-ui/server.py && \
    grep -q 'upgrade_package' agy-ui/server.py && \
    grep -q 'upgrade_all_packages' agy-ui/server.py
    ```

- [ ] **AC-6.3: Package Manager SPA Sub-View in `index.html`**
  - Verify `#viewPackages`, `#btnOpenSettingsPackages`, `#btnUpdateAllPackages`, and package card template exist with Font Awesome vector SVGs.
  - **Verification Gate**:
    ```bash
    grep -q 'id="viewPackages"' agy-ui/index.html && \
    grep -q 'btnOpenSettingsPackages' agy-ui/index.html && \
    grep -q 'btnUpdateAllPackages' agy-ui/index.html
    ```

- [ ] **AC-6.4: Install Package Modal Sheet & Touch Ergonomics**
  - Verify `#modalInstallPackage`, `#inputPackageName`, `#btnConfirmInstallPackage`, preset chips, and safe-area paddings are defined.
  - **Verification Gate**:
    ```bash
    grep -q 'id="modalInstallPackage"' agy-ui/index.html && \
    grep -q 'inputPackageName' agy-ui/index.html && \
    grep -q 'btnConfirmInstallPackage' agy-ui/index.html
    ```

- [ ] **AC-6.5: AI Turn Handoff & Disambiguation Prompt**
  - Verify `server.py` handles `install_package_prompt` by creating an interactive turn with the package disambiguation prompt referencing `ask_question`.
  - **Verification Gate**:
    ```bash
    grep -q 'install_package_prompt' agy-ui/server.py && \
    grep -q 'ask_question' agy-ui/server.py
    ```

- [ ] **AC-6.6: Package Name Validation, Protected Package Guard & Lock Serialization**
  - Verify `server.py` enforces regex validation (`APK_PKG_NAME_RE`), blocks deletion of `PROTECTED_PACKAGES`, serializes mutations with `asyncio.Lock()`, executes via `argv` arrays without shell interpolation, and strips `/etc/apk/world` constraints.
  - **Verification Gate**:
    ```bash
    python3 -c '
    import sys; sys.path.insert(0, "agy-ui")
    import server
    assert hasattr(server, "PROTECTED_PACKAGES"), "Missing PROTECTED_PACKAGES"
    assert "apk-tools" in server.PROTECTED_PACKAGES and "musl" in server.PROTECTED_PACKAGES
    for bad in ["nodejs; id", "../etc/passwd", "--initdb", ""]:
        try:
            server.validate_pkg_name(bad)
            raise AssertionError(f"Failed to reject malicious package name: {bad}")
        except ValueError:
            pass
    print("AC-6.6 PASSED: Security validation and protected package guards verified")
    '
    ```

