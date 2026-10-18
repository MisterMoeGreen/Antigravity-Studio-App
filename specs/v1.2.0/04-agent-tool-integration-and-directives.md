# Specification 04: Agent Tool Integration & System Directives

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.2.0` (Interactive Questions & Touch Decision Modals)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)

---

## 1. Overview & Dual Discovery Mechanism

For the agent to leverage interactive decision dialogs instead of asking open-ended text questions in chat, the capability must be:
1. **Explicitly Registered as an Executable Tool**: Staged at `/data/data/ai.agy.app/files/usr/share/agy-ui/ask_question.py` with symlink `/data/data/ai.agy.app/files/usr/bin/ask_question -> ../share/agy-ui/ask_question.py` on `$PATH`.
2. **Authoritatively Directived in System Prompts**: Anchored inside [`default_app_prompt.md`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/default_app_prompt.md) and synchronized to `~/.gemini/config/GEMINI.md` with an explicit negative constraint prohibiting the built-in `ask_question` tool (which auto-skips in headless print mode) and mandating invocation of `ask_question.py` via `run_command`.

---

## 2. Global Skill Specification (`skills/ask-question/SKILL.md`)

A persistent skill is seeded into `~/.gemini/config/skills/ask-question/SKILL.md` and packaged into assets:

```yaml
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
```

---

## 3. System Prompt Directive Addition (`default_app_prompt.md`)

Section 7 is added to [`agy-ui/default_app_prompt.md`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/default_app_prompt.md) and seeded to `~/.gemini/config/GEMINI.md`:

```markdown
## 7. Interactive Decision Prompts & User Clarifications
- When you need to ask the user a multiple-choice question, clarify ambiguous requirements, or pick an architectural strategy from a list of options:
- NEVER ask the user to type answers manually in the chat prompt if choices can be pre-enumerated.
- CRITICAL: NEVER call the built-in `ask_question` tool declaration directly! In headless print mode, built-in question tools auto-skip instantaneously.
- Instead, invoke the interactive decision bridge via `run_command` using `ask_question` (or `/data/data/ai.agy.app/files/usr/share/agy-ui/ask_question.py`):
  ```bash
  ask_question << 'EOF'
  {"questions": [{"question": "...", "options": ["(Recommended) Option A", "Option B"], "is_multi_select": false}]}
  EOF
  ```
- This triggers an interactive mobile bottom-sheet dialog with touch cards, radio buttons, checkboxes, and a custom write-in field.
- Your execution will pause until the user taps their selection on their smartphone screen, after which the selection is returned directly to you.
- Guidelines:
  - List the optimal option first with the prefix `(Recommended)`.
  - Format choices clearly and concisely for a narrow mobile screen.
  - Do NOT manually add an "Other" option; the dialog automatically includes a write-in field.
  - Avoid asking trivial single-word yes/no questions; proceed autonomously unless an actual architectural decision or user preference is needed.
```

---

## 4. Subprocess Execution & Environment Pass-Through

### 4.1 Path Resolution & Symlink Staging (M-01)
`ask_question.py` is installed to `/data/data/ai.agy.app/files/usr/share/agy-ui/ask_question.py` with `chmod 0755`.
To ensure agents can invoke `ask_question` regardless of their current working directory (e.g. inside `/storage/emulated/0/Documents/...`), `prepare_binaries.sh` creates a symlink:
```bash
ln -sf ../share/agy-ui/ask_question.py app/src/main/assets/usr/bin/ask_question
```
Since `usr/bin` is in `$PATH`, `ask_question` resolves cleanly from any directory.

### 4.2 Dynamic Web Port Agility
When `server.py` spawns `agy`, it exports `AGY_WEB_PORT` into `proc_env`:
```python
proc_env["AGY_WEB_PORT"] = str(self.port)
```
This ensures `ask_question.py` dynamically contacts the exact bound daemon port (`8081`, `8082`, etc.) even when running under fallback port allocations.
