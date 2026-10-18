#!/usr/bin/env python3
"""
Antigravity Studio Interactive Question Bridge Tool (v1.2.0)
Suspends agent tool execution until the mobile user responds via the touch modal.
"""
import sys
import os
import json
import time
import urllib.request
import urllib.error


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _format_output(created_at, completed_at, body_lines):
    if body_lines.startswith("Created At:"):
        return body_lines
    return f"Created At: {created_at}\nCompleted At: {completed_at}\n{body_lines}"


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

    payload = {
        "questions": normalized_questions,
        "tool_action": data.get("tool_action") or data.get("toolAction") or "Waiting for user decision",
        "tool_summary": data.get("tool_summary") or data.get("toolSummary") or "Decision required",
        "timeout_seconds": int(data.get("timeout_seconds") or data.get("timeoutSeconds") or 300)
    }

    port = os.environ.get("AGY_WEB_PORT", "8081")
    url = f"http://127.0.0.1:{port}/api/question/ask"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=payload["timeout_seconds"] + 30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            ans = res.get("formatted_output", "A1: User Skipped")
            print(_format_output(created_at, _iso_now(), ans))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            ans = err_body.get("formatted_output", "A1: User Timed Out")
        except Exception:
            ans = "A1: User Skipped"
        print(_format_output(created_at, _iso_now(), ans))
    except Exception:
        print(_format_output(created_at, _iso_now(), "A1: User Skipped"))


if __name__ == "__main__":
    main()
