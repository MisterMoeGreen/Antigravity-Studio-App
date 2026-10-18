# Specification 02: Backend API & Bridge Protocol

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.2.0` (Interactive Questions & Touch Decision Modals)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)  
> **Branch**: `v1.2.0`  
> **Review Status**: **ARCHITECTURALLY AUDITED & HARDENED BY GEMINI 4 ARGON (100% Mitigations Integrated)**

---

## 1. Overview & Protocol Design

This specification defines the communication contracts and data models for:
1. **The Question Bridge API in [`server.py`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/server.py)**: Async REST endpoints managing synchronous tool wait states using `asyncio.Future`.
2. **The Question Bridge Client Executable (`ask_question.py`)**: A lightweight Python script staged in `usr/share/agy-ui/ask_question.py` (and symlinked into `usr/bin/ask_question`) executed by the agent to suspend execution.
3. **The WebSocket Event Envelope**: Broadcast frames notifying the PWA frontend of pending user questions and state updates.

---

## 2. REST Endpoints Specification

### A. Ask Question Endpoint (`POST /api/question/ask`)

Called synchronously by `ask_question.py` when the agent invokes the tool.

- **Request Headers**:
  - `Content-Type: application/json`
- **Request Body Schema**:
  ```json
  {
    "questions": [
      {
        "question": "Which architecture pattern should we use?",
        "options": [
          "(Recommended) Clean Architecture with UseCases",
          "Layered Model-View-Presenter (MVP)",
          "Single-file Modular Script"
        ],
        "is_multi_select": false
      }
    ],
    "tool_action": "Selecting architecture pattern",
    "tool_summary": "Architecture decision",
    "timeout_seconds": 300
  }
  ```
- **Backend Normalization & Permissive Parsing (`M-11`)**:
  - Accepts both `camelCase` and `snake_case` keys: `tool_action`/`toolAction`, `tool_summary`/`toolSummary`, `is_multi_select`/`isMultiSelect`, `timeout_seconds`/`timeoutSeconds`.
  - Automatically wraps top-level shorthand `{"question": "...", "options": [...]}` into `{"questions": [...]}`.
  - Automatically populates default fallback options `["(Recommended) Proceed", "Skip"]` if options list is empty.
- **Backend Behavior**:
  1. Validates `questions` array.
  2. Generates an alphanumeric `question_id = f"q-{uuid.uuid4().hex[:8]}"`.
  3. Creates an `asyncio.Future()` registered in `self._pending_questions[question_id] = {"future": future, "session_id": session_id, "created_at": time.time()}`.
  4. Broadcasts WebSocket event `{"type": "ask_question", "question_id": question_id, "payload": ...}` to all active web clients.
  5. Awaits the future with timeout `asyncio.wait_for(future, timeout=timeout_seconds)`.
  6. **Cancellation & Disconnect Protection (`M-08`)**: The `finally:` block cleans up `_pending_questions[question_id]`. If cancelled (e.g. client drops or shell terminates), it broadcasts `{"type": "dismiss_question", "question_id": question_id, "reason": "client_disconnected"}`.
- **Response Body Schema (Success - 200 OK)**:
  ```json
  {
    "status": "success",
    "question_id": "q-1a2b3c4d",
    "skipped": false,
    "answers": [
      "Clean Architecture with UseCases"
    ],
    "formatted_output": "A1: Clean Architecture with UseCases"
  }
  ```
- **Response Body Schema (Skipped - 200 OK)**:
  ```json
  {
    "status": "skipped",
    "question_id": "q-1a2b3c4d",
    "skipped": true,
    "answers": [],
    "formatted_output": "A1: User Skipped"
  }
  ```
- **Response Body Schema (Timeout - 200 OK with status timeout - `M-02`)**:
  ```json
  {
    "status": "timeout",
    "question_id": "q-1a2b3c4d",
    "skipped": true,
    "answers": [],
    "formatted_output": "A1: User Timed Out (no response received within 300s)"
  }
  ```

---

### B. Respond to Question Endpoint (`POST /api/question/respond`)

Called by the frontend UI when the user taps **Submit** or **Skip**.

- **Request Body Schema (`M-05`)**:
  ```json
  {
    "question_id": "q-1a2b3c4d",
    "skipped": false,
    "responses": [
      {
        "question_index": 0,
        "selected": ["(Recommended) Clean Architecture with UseCases"],
        "write_in": "Keep dependencies lightweight"
      }
    ],
    "answers": [
      "(Recommended) Clean Architecture with UseCases"
    ],
    "write_in": "Keep dependencies lightweight"
  }
  ```
- **Formatting Standard**:
  - For single questions with `is_multi_select: true`, `server.py` joins selections under a single line: `A1: Option 1, Option 2`.
  - If custom write-in text is provided alongside a selection: `A1: Option 1 (User Note: write-in text)`.
  - If ONLY write-in text is provided: `A1: write-in text`.
  - For multiple questions, produces sequential lines: `A1: ...\nA2: ...`.
- **Response Body Schema**:
  ```json
  {
    "status": "ok",
    "message": "Question response accepted"
  }
  ```
- **Error Codes**:
  - `404 Not Found`: `question_id` not found or already resolved/timed out.
  - `400 Bad Request`: Payload missing required fields.

---

### C. Pending Questions Query (`GET /api/question/pending`)

Used by the frontend upon page reconnect to rehydrate active modals.

- **Response Body Schema**:
  ```json
  {
    "pending": [
      {
        "question_id": "q-1a2b3c4d",
        "created_at": 1729273200,
        "questions": [...],
        "tool_action": "Selecting architecture pattern",
        "tool_summary": "Architecture decision"
      }
    ]
  }
  ```

---

## 3. WebSocket Event Contracts

### A. Question Display Broadcast
Emitted immediately when `POST /api/question/ask` registers a new question:
```json
{
  "type": "ask_question",
  "question_id": "q-1a2b3c4d",
  "created_at": 1729273200,
  "tool_action": "Selecting architecture pattern",
  "tool_summary": "Architecture decision",
  "questions": [
    {
      "question": "Which architecture pattern should we use?",
      "options": [
        "(Recommended) Clean Architecture with UseCases",
        "Layered Model-View-Presenter (MVP)",
        "Single-file Modular Script"
      ],
      "is_multi_select": false
    }
  ]
}
```

### B. Question Dismiss Broadcast (`M-08`, `M-10`)
Emitted if a pending question is cancelled by turn abort, session switch, or timeout:
```json
{
  "type": "dismiss_question",
  "question_id": "q-1a2b3c4d",
  "reason": "interrupted"
}
```

---

## 4. Hardened Bridge Client Implementation (`ask_question.py`)

The helper script [`usr/share/agy-ui/ask_question.py`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/server.py) incorporates all Argon mitigations (`M-02`, `M-03`, `M-06`, `M-07`, `M-11`):

```python
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
    if not questions and data.get("question"):
        questions = [{
            "question": data.get("question"),
            "options": data.get("options", ["(Recommended) Yes", "No"]),
            "is_multi_select": bool(data.get("is_multi_select") or data.get("isMultiSelect", False))
        }]

    normalized_questions = []
    for q in (questions or []):
        if isinstance(q, dict) and q.get("question"):
            opts = q.get("options") or ["(Recommended) Proceed", "Skip"]
            normalized_questions.append({
                "question": str(q["question"]),
                "options": [str(o) for o in opts],
                "is_multi_select": bool(q.get("is_multi_select") or q.get("isMultiSelect", False))
            })

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
```
