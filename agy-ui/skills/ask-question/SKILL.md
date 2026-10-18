---
name: ask-question
description: Present interactive single-choice or multi-select decision dialogs to the mobile user with predefined options, recommended defaults, and custom write-in support.
---

# Interactive Decision Prompts

Use this skill when you need the mobile user to make an explicit design choice, select an implementation option, or clarify ambiguous requirements without forcing them to type manually on a touch keyboard.

## Critical Execution Constraint (Built-in Tool Collision)
- **CRITICAL**: Do NOT call the built-in `ask_question` tool directly! The underlying CLI runtime runs in headless print mode (`-p`), which immediately auto-skips built-in question tools (`A1: User Skipped` in 17ms).
- **ALWAYS** invoke the interactive question bridge script via `run_command` using either the `$PATH` binary `ask_question` or its absolute path:
  `/data/data/ai.agy.app/files/usr/share/agy-ui/ask_question.py`

## Execution Syntax
Because user options or questions often contain quotes, parentheses, and special characters, prefer piping via heredoc to prevent shell quoting errors:

```bash
ask_question << 'EOF'
{
  "questions": [
    {
      "question": "Which database engine should we configure?",
      "options": [
        "(Recommended) SQLite with WAL mode",
        "PostgreSQL via Remote Container",
        "In-Memory Dictionary"
      ],
      "is_multi_select": false
    }
  ],
  "toolAction": "Selecting database engine",
  "toolSummary": "Database setup decision"
}
EOF
```

Alternatively, invoke via inline JSON argument when concise:
```bash
ask_question '{"questions": [{"question": "Proceed with deployment?", "options": ["(Recommended) Yes, proceed", "No, cancel"], "is_multi_select": false}]}'
```

The script suspends execution, triggers the native touch modal on the user's mobile screen, and prints the user's exact selection to stdout upon completion:
```text
Created At: 2026-10-18T18:00:00Z
Completed At: 2026-10-18T18:00:12Z
A1: (Recommended) SQLite with WAL mode
```

## Guidelines:
1. Always list recommended options first and prefix them with `(Recommended)`.
2. Do NOT add an "Other" option; the mobile UI automatically provides an expandable write-in textarea.
3. Keep question text and option labels concise for narrow mobile screens.
4. Set `"is_multi_select": true` when multiple items may be checked simultaneously.
5. The bridge accepts normalized keys: `"question"` or `"title"`, `"options"` or `"choices"`, and `"is_multi_select"` or `"isMultiSelect"`.
