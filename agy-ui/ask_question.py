#!/usr/bin/env python3
"""
Antigravity Studio Interactive Question Bridge Tool (v1.2.0)
Suspends agent tool execution until the mobile user responds via the touch modal.
Communicates via UNIX domain socket with backend server daemon.
"""
import sys
import os
import json
import time
import socket
from pathlib import Path


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _format_output(created_at, completed_at, body_lines):
    if body_lines.startswith("Created At:"):
        return body_lines
    return f"Created At: {created_at}\nCompleted At: {completed_at}\n{body_lines}"


def _get_socket_path() -> Path:
    if os.environ.get("AGY_QUESTION_SOCK"):
        return Path(os.environ["AGY_QUESTION_SOCK"])

    candidates = [
        Path(os.environ.get("HOME", "/data/data/ai.agy.app/files/home")) / ".gemini" / "antigravity-cli" / "ask_question.sock",
        Path.home() / ".gemini" / "antigravity-cli" / "ask_question.sock",
        Path("/data/data/ai.agy.app/files/home/.gemini/antigravity-cli/ask_question.sock"),
        Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/ask_question.sock"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def main():
    created_at = _iso_now()
    raw_input = ""
    if len(sys.argv) > 1 and sys.argv[1] != "--stdin":
        raw_input = " ".join(sys.argv[1:]).strip()
    elif not sys.stdin.isatty():
        raw_input = sys.stdin.read().strip()

    if not raw_input:
        print(_format_output(created_at, _iso_now(), "A1: User Skipped (empty question payload)"))
        return

    try:
        data = json.loads(raw_input)
    except Exception:
        # Fallback if raw string question was passed instead of JSON
        data = {
            "questions": [{
                "question": raw_input,
                "options": ["(Recommended) Proceed", "Cancel"],
                "is_multi_select": False
            }]
        }

    # Normalize single-question shorthand & camelCase keys (M-11)
    questions = data.get("questions")
    if not questions and (data.get("question") or data.get("title")):
        questions = [{
            "question": data.get("question") or data.get("title"),
            "options": data.get("options") or data.get("choices") or ["(Recommended) Proceed", "Skip"],
            "is_multi_select": bool(data.get("is_multi_select") or data.get("isMultiSelect", False))
        }]

    normalized_questions = []
    for q in (questions or []):
        if isinstance(q, dict) and (q.get("question") or q.get("title")):
            raw_opts = q.get("options") or q.get("choices") or ["(Recommended) Proceed", "Skip"]
            opts = [str(o).strip() for o in raw_opts if str(o).strip()]
            if not opts:
                opts = ["(Recommended) Proceed", "Skip"]
            normalized_questions.append({
                "question": str(q.get("question") or q.get("title")),
                "options": opts,
                "is_multi_select": bool(q.get("is_multi_select") or q.get("isMultiSelect", False))
            })

    if not normalized_questions:
        print(_format_output(created_at, _iso_now(), "A1: User Skipped (no valid questions provided)"))
        return

    timeout_sec = int(data.get("timeout_seconds") or data.get("timeoutSeconds") or 300)
    payload = {
        "action": "ask",
        "questions": normalized_questions,
        "tool_action": data.get("tool_action") or data.get("toolAction") or "Waiting for user decision",
        "tool_summary": data.get("tool_summary") or data.get("toolSummary") or "Decision required",
        "timeout_seconds": timeout_sec
    }

    sock_path = _get_socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec + 30)
        sock.connect(str(sock_path))
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")

        file_obj = sock.makefile("r", encoding="utf-8")
        line = file_obj.readline()
        sock.close()

        if line:
            res = json.loads(line)
            ans = res.get("formatted_output", "A1: User Skipped")
            print(_format_output(created_at, _iso_now(), ans))
        else:
            print(_format_output(created_at, _iso_now(), "A1: User Skipped (empty daemon response)"))
    except socket.timeout:
        print(_format_output(created_at, _iso_now(), f"A1: User Timed Out (no response received within {timeout_sec}s)"))
    except Exception as exc:
        # Fallback to User Skipped if daemon unreachable
        print(_format_output(created_at, _iso_now(), f"A1: User Skipped ({exc})"))


if __name__ == "__main__":
    main()
