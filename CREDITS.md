# Credits, Attributions & Legal Disclaimers

## Legal Disclaimer & Non-Affiliation Notice

**Antigravity Studio** is an independent, community-developed open-source client application and native Android runtime wrapper. 

* **Trademarks**: "Antigravity", "Google", "Android", and related marks are trademarks or registered trademarks of Google LLC.
* **Non-Affiliation**: This project is **not** officially affiliated with, maintained by, sponsored by, or endorsed by Google LLC or Google DeepMind.
* **Terms of Service**: Use of the Antigravity engine and Gemini API backends is governed by Google’s respective Terms of Service and API Acceptable Use Policies.

---

## Upstream Components & Attributions

### 1. Antigravity Agent Engine
* **Copyright**: &copy; Google LLC / Google DeepMind
* **Upstream**: Distributed via official Antigravity distribution channels (`https://antigravity.google/cli/install.sh`).
* **Usage**: Downloaded on-demand to private app storage (`usr/opt/agy/`) and dispatched via the native `libagy_launcher.so` bridge to provide core agentic planning, reasoning, and tool dispatching at native ARM64 speed.

### 2. GNU Project
* **GNU Bash (`libbash.so`)**:
  * **Copyright**: &copy; Free Software Foundation, Inc.
  * **License**: GNU General Public License v3 (GPLv3)
* **GNU C Library (`libglibc_*.so`)**:
  * **Copyright**: &copy; Free Software Foundation, Inc.
  * **License**: GNU Lesser General Public License v2.1 (LGPLv2.1)

### 3. Git Version Control System (`libgit.so`, `libgit_remote_https.so`)
* **Copyright**: &copy; Linus Torvalds, Junio C Hamano, and the Git Development Community
* **License**: GNU General Public License v2 (GPLv2)

### 4. Python Programming Language (`libpython3_bin.so`, Python C-Extensions)
* **Copyright**: &copy; Python Software Foundation
* **License**: Python Software Foundation License Version 2.0 (PSFL)

### 5. BusyBox Multi-Call Utility (`libbusybox.so`)
* **Copyright**: &copy; Erik Andersen, Denys Vlasenko, and the BusyBox Contributors
* **License**: GNU General Public License v2 (GPLv2)

### 6. Ripgrep (`librg.so`)
* **Copyright**: &copy; Andrew Gallant (BurntSushi)
* **License**: MIT License / Unlicense

### 7. cURL & libcurl (`libcurl.so`, `libcurl_bin.so`)
* **Copyright**: &copy; 1996 - 2026, Daniel Stenberg and many contributors
* **License**: curl License (MIT / X-style derivative)

### 8. OpenSSL Cryptography (`libssl.so`, `libcrypto.so`)
* **Copyright**: &copy; The OpenSSL Project Authors
* **License**: Apache License 2.0

### 9. Zstandard Compression (`zstd-jni`, `libzstd.so`)
* **Copyright**: &copy; Meta Platforms, Inc. and Luben Karavelov
* **License**: BSD 3-Clause License / Apache License 2.0

### 10. Android Open Source Project (AOSP) & AndroidX
* **Copyright**: &copy; The Android Open Source Project / Google LLC
* **License**: Apache License 2.0

### 11. Font Awesome Free (`fontawesome.com`)
* **Copyright**: &copy; Fonticons, Inc.
* **Website**: [https://fontawesome.com](https://fontawesome.com)
* **License**:
  * **Icons**: [CC BY 4.0 License](https://creativecommons.org/licenses/by/4.0/)
  * **Fonts**: [SIL OFL 1.1 License](http://scripts.sil.org/OFL)
  * **Code**: [MIT License](https://opensource.org/licenses/MIT)
* **Usage**: Application UI vector iconography across toolbars, dialogs, drawers, and status indicators.

### 12. wallentx / `antigravity-cli-termux`
* **Repository**: [https://github.com/wallentx/antigravity-cli-termux](https://github.com/wallentx/antigravity-cli-termux)
* **Author**: wallentx & community contributors
* **Usage**: Discovered and published the VA39 kernel compatibility layer (rewriting TCMalloc 48-bit Virtual Address instructions and bitmasks to fit 39-bit Android memory layouts). Antigravity Studio ingests the official Linux ARM64 engine directly from Google DeepMind's Cloud CDN and applies this VA39 patch autonomously on-device.

### 13. PRoot Virtualization Engine (`libproot.so`, `libproot_loader.so`)
* **Copyright**: &copy; Cédric Vincent and the PRoot Development Community
* **License**: GNU General Public License v2 (GPLv2)
* **Usage**: User-space syscall virtualization and anonymous memory ELF execution enabling native JITs and compilers to execute on Android without root or SELinux W^X violations.

---

## Community Acknowledgments

* **wallentx / `antigravity-cli-termux`**: Gratefully acknowledged for pioneering the initial community exploration and discovering the TCMalloc VA39 memory patch adaptation for Android ARM64 compatibility.
* **Termux Community**: For packaging and maintaining Android-compatible AArch64 ports of developer toolchains.
* **Font Awesome**: Gratefully acknowledged for providing open-source, high-quality vector iconography.
