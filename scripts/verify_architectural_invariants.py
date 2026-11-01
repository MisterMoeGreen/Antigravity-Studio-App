#!/usr/bin/env python3
"""
Pre-build Architectural & Hybrid Invariants Verification Gate.
Executed automatically during Gradle preBuild to prevent regressions.
"""

import ast
import os
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT_DIR / "agy-ui" / "server.py"
INDEX_HTML = ROOT_DIR / "agy-ui" / "index.html"
MAIN_ACTIVITY = ROOT_DIR / "app" / "src" / "main" / "java" / "ai" / "agy" / "app" / "MainActivity.kt"


def check_python_ast_integrity():
    print("[1/4] Verifying Python AST class scoping integrity...")
    if not SERVER_PY.exists():
        print(f"ERROR: {SERVER_PY} does not exist!")
        sys.exit(1)

    with open(SERVER_PY, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"ERROR: Python syntax error in server.py: {e}")
        sys.exit(1)

    orchestrator_class = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgyProcessOrchestrator":
            orchestrator_class = node
            break

    if not orchestrator_class:
        print("ERROR: AgyProcessOrchestrator class not found in server.py AST!")
        sys.exit(1)

    required_methods = {
        "__init__",
        "execute_turn",
        "abort_turn",
        "trigger_in_app_login",
        "submit_auth_code",
        "_on_auth_success",
        "_cleanup_auth",
        "_secure_oauth_token",
        "_consume_stdout_stream",
        "_consume_stderr_stream",
        "_demux_json_event",
    }

    found_methods = {
        n.name
        for n in orchestrator_class.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    missing = required_methods - found_methods
    if missing:
        print(f"ERROR: Class scoping fracture! Methods missing from AgyProcessOrchestrator: {missing}")
        print("An unindented function likely terminated class scope prematurely.")
        sys.exit(1)

    print(f"  ✓ AgyProcessOrchestrator AST verified ({len(found_methods)} methods encapsulated).")


def check_webview_api_boundaries():
    print("[2/4] Verifying Android WebView hybrid API barriers...")
    if not INDEX_HTML.exists():
        print(f"ERROR: {INDEX_HTML} does not exist!")
        sys.exit(1)

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    # Rule: navigator.clipboard.readText must be shielded by native AndroidApp.getClipboardText check
    if "navigator.clipboard.readText" in html:
        if "window.AndroidApp.getClipboardText" not in html and "AndroidApp.getClipboardText" not in html:
            print("ERROR: Insecure clipboard read! navigator.clipboard.readText() used without AndroidApp native bridge fallback.")
            print("Android WebView blocks navigator.clipboard.readText() unless bridged to native Android ClipboardManager.")
            sys.exit(1)

    # Rule: window.open must be shielded by AndroidApp.openUrl check
    if "window.open(" in html:
        if "window.AndroidApp.openUrl" not in html and "AndroidApp.openUrl" not in html:
            print("ERROR: Insecure browser navigation! window.open() used without AndroidApp.openUrl native bridge.")
            print("Android WebView silently drops window.open() unless handled via CustomTabs native bridge.")
            sys.exit(1)

    print("  ✓ Android WebView native API boundaries verified.")


def check_native_bridge_contract():
    print("[3/4] Verifying Native Android Kotlin bridge contracts...")
    if not MAIN_ACTIVITY.exists():
        print(f"ERROR: {MAIN_ACTIVITY} does not exist!")
        sys.exit(1)

    with open(MAIN_ACTIVITY, "r", encoding="utf-8") as f:
        kt = f.read()

    required_bridges = [
        "fun openUrl(url: String)",
        "fun getClipboardText(): String",
        "fun setClipboardText(text: String)",
    ]

    for b in required_bridges:
        if b not in kt:
            print(f"ERROR: Missing required @JavascriptInterface bridge: '{b}' in MainActivity.kt!")
            sys.exit(1)

    print("  ✓ Native Kotlin bridge interface contracts verified.")


def check_javascript_syntax():
    print("[4/4] Verifying JavaScript syntax in index.html...")
    if not INDEX_HTML.exists():
        print(f"ERROR: {INDEX_HTML} does not exist!")
        sys.exit(1)

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    try:
        import esprima
        script_matches = list(re.finditer(r'<script(?:\s+[^>]*)?>(.*?)</script>', html, re.DOTALL))
        for m in script_matches:
            start_pos = m.start(1)
            line_offset = html[:start_pos].count('\n') + 1
            script_text = m.group(1)
            if not script_text.strip():
                continue
            try:
                esprima.parseScript(script_text, {'tolerant': False})
            except Exception as e:
                print(f"ERROR: JavaScript syntax error in index.html (near line {line_offset}):\n{e}")
                sys.exit(1)
        print(f"  ✓ JavaScript syntax verified across {len(script_matches)} script blocks (esprima).")
    except ImportError:
        print("  ⚠ esprima not installed; skipping deep JS AST syntax check.")


if __name__ == "__main__":
    print("=== Running Antigravity Studio Pre-Build Verification Gate ===")
    check_python_ast_integrity()
    check_webview_api_boundaries()
    check_native_bridge_contract()
    check_javascript_syntax()
    print("=== All Architectural Invariants Verified Successfully! ===\n")
