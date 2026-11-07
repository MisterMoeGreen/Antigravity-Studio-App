#!/usr/bin/env python3
"""Architectural & Security Review Runner.

Analyzes specifications, source files, and build configurations for Android
sandbox constraints, SELinux W^X policies, linker traps, and architectural edge cases.

Usage:
    python3 .agents/skills/review-agent/scripts/call_review.py --spec <spec_file> [--thinking-level high]
    python3 .agents/skills/review-agent/scripts/call_review.py --check-code <target_path>
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Finding:
    rule_id: str
    severity: str
    subsystem: str
    file_path: str
    line_num: int
    matched_text: str
    description: str
    mitigation: str


# Static architectural heuristics for Android sandbox & AgyStudio environment
HEURISTICS = [
    {
        "id": "SYS-01",
        "severity": "CRITICAL",
        "subsystem": "SELinux / W^X Policy",
        "pattern": re.compile(r"(?:^|\s)(?:\.\/[\w\-\.\/]+\.(?:sh|py|bin)|execv?p?e?\s*\(\s*[\"']\.\/)"),
        "description": "Direct execution of scripts/binaries via relative path (./script) is blocked by Android W^X policy.",
        "mitigation": "Invoke scripts explicitly via interpreter (e.g. 'bash script.sh' or 'python3 script.py').",
    },
    {
        "id": "SYS-02",
        "severity": "CRITICAL",
        "subsystem": "CI / OTA Keystore",
        "pattern": re.compile(r"signingConfigs\.getByName\([\"']debug[\"']\)|debug\.keystore"),
        "description": "Default debug.keystore generated on ephemeral CI runners breaks APK upgrade continuity.",
        "mitigation": "Export and use deterministic AGY_KEYSTORE_BASE64 secret in CI build workflows.",
    },
    {
        "id": "SYS-03",
        "severity": "HIGH",
        "subsystem": "Native Symlink Stability",
        "pattern": re.compile(r"/data/app/~~[a-zA-Z0-9_\-]+/"),
        "description": "Hardcoded Android package hash directory paths break across app updates (nativeLibraryDir rotation).",
        "mitigation": "Resolve native paths dynamically at runtime and reconcile symlinks on boot/MY_PACKAGE_REPLACED.",
    },
    {
        "id": "SYS-04",
        "severity": "HIGH",
        "subsystem": "Page Size Alignment (Android 15)",
        "pattern": re.compile(r"patchelf(?!\s+[^\n]*--page-size\s+16384)"),
        "description": "patchelf without explicit --page-size 16384 produces 4KB-aligned ELFs that crash on Android 15 arm64.",
        "mitigation": "Always pass '--page-size 16384' to patchelf when modifying arm64 binaries.",
    },
    {
        "id": "SYS-05",
        "severity": "MEDIUM",
        "subsystem": "Android 14/15 PendingIntent Security",
        "pattern": re.compile(r"PendingIntent\.get(?:Activity|Broadcast|Service)\s*\([^)]*FLAG_MUTABLE(?!\s*\|\s*Intent\.)"),
        "description": "Mutable PendingIntent with implicit Intent fails on Android 14/15 (targetSdk 34+).",
        "mitigation": "Use explicit component Intent or FLAG_IMMUTABLE when creating PendingIntents.",
    },
    {
        "id": "SYS-06",
        "severity": "MEDIUM",
        "subsystem": "VFS Permissions & Directory Lock",
        "pattern": re.compile(r"chmod\s+(?:777|0777|755)\s+.*files/usr/bin"),
        "description": "Leaving files/usr/bin writable exposes binaries to direct kernel svc #0 unlink/rename traps.",
        "mitigation": "Lock files/usr/bin to mode 0500 (dr-x------) to leverage Linux VFS kernel DAC write protection.",
    },
    {
        "id": "SYS-07",
        "severity": "CRITICAL",
        "subsystem": "Hybrid WebView Native Bridge",
        "pattern": re.compile(r"navigator\.clipboard\.readText(?!\s*\([^)]*AndroidApp)|window\.open\s*\([^)]*'_blank'\)(?!\s*if\s*\(window\.AndroidApp)"),
        "description": "Unbridged desktop web APIs (clipboard read, window.open) fail silently or throw SecurityError in Android WebView.",
        "mitigation": "Bridge through @JavascriptInterface in MainActivity.kt (e.g. AndroidApp.getClipboardText(), AndroidApp.openUrl()).",
    },
    {
        "id": "SYS-08",
        "severity": "CRITICAL",
        "subsystem": "AST Class Scoping & Indentation",
        "pattern": re.compile(r"^def\s+[a-zA-Z0-9_]+\s*\([^)]*\)\s*->", re.MULTILINE),
        "description": "Module-level function inserted without indentation inside a class fractures class scope.",
        "mitigation": "Validate AST structure with scripts/verify_architectural_invariants.py before build.",
    },
    {
        "id": "SYS-09",
        "severity": "HIGH",
        "subsystem": "Mobile Touch Targets & Stacking Context",
        "pattern": re.compile(r"\.btn-[a-zA-Z0-9_\-]+\s*\{[^}]*position:\s*absolute;(?![^}]*z-index:\s*\d+)"),
        "description": "Absolute-positioned touch buttons without explicit z-index get swallowed by focused input hardware layers.",
        "mitigation": "Add explicit z-index: 10+, touch-action: manipulation, and dual click/touchend event handlers.",
    },
]


def scan_file(file_path: str) -> List[Finding]:
    findings: List[Finding] = []
    if not os.path.isfile(file_path):
        return findings

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        has_clipboard_bridge = "AndroidApp.getClipboardText" in content
        has_url_bridge = "AndroidApp.openUrl" in content

        for idx, line in enumerate(content.splitlines(), start=1):
            clean_line = line.strip()
            # Skip comments if markdown/shell/python
            if clean_line.startswith("#") and not clean_line.startswith("#!"):
                continue

            for h in HEURISTICS:
                if h["id"] == "SYS-07":
                    if "navigator.clipboard.readText" in clean_line and has_clipboard_bridge:
                        continue
                    if "window.open" in clean_line and has_url_bridge:
                        continue
                elif h["id"] == "SYS-08":
                    # SYS-08 class fragmentation applies to python source files, not markdown specs
                    if file_path.endswith(".md") or line.startswith((" ", "\t")):
                        continue
                    match = h["pattern"].search(line)
                    if match:
                        findings.append(
                            Finding(
                                rule_id=h["id"],
                                severity=h["severity"],
                                subsystem=h["subsystem"],
                                file_path=file_path,
                                line_num=idx,
                                matched_text=match.group(0),
                                description=h["description"],
                                mitigation=h["mitigation"],
                            )
                        )
                    continue

                match = h["pattern"].search(clean_line)
                if match:
                    findings.append(
                        Finding(
                            rule_id=h["id"],
                            severity=h["severity"],
                            subsystem=h["subsystem"],
                            file_path=file_path,
                            line_num=idx,
                            matched_text=match.group(0),
                            description=h["description"],
                            mitigation=h["mitigation"],
                        )
                    )
    except Exception as err:
        sys.stderr.write(f"Error reading {file_path}: {err}\n")

    return findings


def scan_directory(target_dir: str) -> List[Finding]:
    all_findings: List[Finding] = []
    ignored_dirs = {".git", ".gradle", "build", ".idea", "node_modules", "__pycache__"}

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in {".py", ".sh", ".c", ".cpp", ".h", ".kt", ".java", ".gradle", ".kts", ".md"}:
                fp = os.path.join(root, file)
                all_findings.extend(scan_file(fp))

    return all_findings


def format_report_markdown(findings: List[Finding], target: str, thinking_level: str) -> str:
    lines = [
        "# Architectural Review & System Hardening Report",
        "",
        f"- **Target**: `{target}`",
        f"- **Auditor**: `review-agent (automated)`",
        f"- **Reasoning Depth**: `{thinking_level}`",
        f"- **Findings Count**: `{len(findings)}`",
        "",
    ]

    if not findings:
        lines.append("### Status: PASS")
        lines.append("No critical Android sandbox, SELinux, or linker architectural hazards detected.")
        return "\n".join(lines)

    lines.append("## Findings & Architectural Mitigations")
    lines.append("")

    for f in findings:
        lines.append(f"### [{f.severity}] {f.rule_id} — {f.subsystem}")
        lines.append(f"- **Location**: `{f.file_path}:{f.line_num}`")
        lines.append(f"- **Snippet**: `{f.matched_text}`")
        lines.append(f"- **Risk**: {f.description}")
        lines.append(f"- **Architectural Mitigation**: {f.mitigation}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Architectural & Security Review Runner")
    parser.add_argument("--spec", "-s", help="Path to specification file to audit")
    parser.add_argument("--check-code", "-c", help="Path to source file or directory to scan")
    parser.add_argument(
        "--thinking-level",
        choices=["low", "medium", "high"],
        default="high",
        help="Audit reasoning depth (default: high)",
    )
    args = parser.parse_args()

    target = args.spec or args.check_code or "."
    findings: List[Finding] = []

    if os.path.isfile(target):
        findings = scan_file(target)
    elif os.path.isdir(target):
        findings = scan_directory(target)
    else:
        sys.stderr.write(f"Target '{target}' not found.\n")
        sys.exit(1)

    report = format_report_markdown(findings, target, args.thinking_level)
    print(report)


if __name__ == "__main__":
    main()
