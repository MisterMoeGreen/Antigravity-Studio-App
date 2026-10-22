# Specification 03: Sidebar Branding, Status Indicators & Deduplication

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.4.0` (Parallel Session Concurrency & Background Execution)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)  
> **Branch**: `v1.4.0`  
> **Review Status**: **ARCHITECTURALLY AUDITED & HARDENED BY GEMINI 4 ARGON (`ARGON-V14-11`, `ARGON-V14-18`)**

---

## 1. Problem Statement & Root Cause

The session navigation drawer in [`agy-ui/index.html`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/index.html) suffers from three distinct ergonomic and visual shortcomings:

1. **Generic Drawer Header**:
   The sidebar header renders a plain `<span>Conversations</span>` text title. It lacks brand identity and application context.
2. **Missing Real-Time Session Status**:
   Users navigating between multiple conversations have no visual indication of:
   - Which conversations have active turns running in the background.
   - Which conversations are paused waiting for user input on an interactive question (`ARGON-V14-11`).
   - Which conversations finished generating a new response that has not yet been viewed.
3. **Session Title & Preview Duplication**:
   In `renderSessionDrawerList()`, each conversation card renders:
   ```html
   <div class="session-card-title">${escapeHtml(s.title || 'Untitled Session')}</div>
   <div class="session-card-preview">${escapeHtml(s.preview || s.conversation_id)}</div>
   ```
   Because `conversation_summaries.db` in SQLite stores the initial prompt in both `title` and `preview`, both lines display the exact same text—first in bold, and directly below in regular text. Furthermore, if `preview` contains raw Markdown (`**`, backticks, `#`), naive string matching fails (`ARGON-V14-18`).

---

## 2. Core Objectives

1. **Sidebar Header Branding**:
   Replace the generic `<span>Conversations</span>` header with the Antigravity Studio app logo (`/icon-192.png`) and application name **"Antigravity Studio"** with clean mobile alignment.
2. **Pulsing Agent Working Indicator**:
   Display an active indicator in the session card when an agent turn is currently running in that session. The indicator matches the pulsing status dot from the top app bar (`.status-dot.running` with CSS pulse animation).
3. **Multi-State Status Badges**:
   - **Amber "Needs Input" Badge**: When an agent is waiting for user response on an interactive decision question.
   - **Emerald "New" Badge**: When an agent completes a turn in a background session.
4. **Markdown-Aware Title/Preview Deduplication (`ARGON-V14-18`)**:
   Strip Markdown formatting syntax before comparing `title` and `preview`. If `preview` duplicates `title` (or is redundant with `conversation_id`), suppress the duplicate preview row.

---

## 3. UI Design & Layout Specifications

### 3.1 Drawer Header Branding

```html
<!-- Left Slide-Out Session Drawer Header -->
<div class="drawer-header">
  <div class="drawer-title-row">
    <div class="drawer-brand">
      <img src="/icon-192.png" alt="Antigravity Studio" class="drawer-brand-logo" />
      <span class="drawer-brand-title">Antigravity Studio</span>
    </div>
    <button class="icon-btn" id="btnCloseDrawer" style="width:32px;height:32px;" title="Close drawer">
      <svg class="fa-icon" viewBox="0 0 384 512" aria-hidden="true" focusable="false">
        <path fill="currentColor" d="M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z"/>
      </svg>
    </button>
  </div>
  <!-- Theme segmented control -->
</div>
```

```css
/* Drawer Brand Header Styling */
.drawer-brand {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 32px;
}

.drawer-brand-logo {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  object-fit: contain;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.drawer-brand-title {
  font-family: var(--font-sans);
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.2px;
  color: var(--text-primary);
  white-space: nowrap;
}
```

---

### 3.2 Session Card Status Indicators

Inside each `.session-card`:

```html
<div class="session-card-header">
  <div class="session-card-title-group">
    <div class="session-card-title">${escapeHtml(s.title || 'Untitled Session')}</div>
    ${isRunning && !waitingInput ? `
      <span class="session-indicator-working" title="Agent is working...">
        <span class="status-dot running"></span>
      </span>
    ` : ''}
    ${waitingInput ? `
      <span class="session-badge-input" title="Waiting for user input">Needs Input</span>
    ` : ''}
    ${isUnread ? `
      <span class="session-badge-unread" title="New response available">New</span>
    ` : ''}
  </div>
  <button class="session-menu-btn" data-idx="${idx}" title="Session options">⋯</button>
</div>
```

```css
.session-card-title-group {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.session-indicator-working {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-left: 2px;
}

.session-badge-unread {
  display: inline-block;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  border-radius: 999px;
  background: rgba(16, 185, 129, 0.18);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.35);
  flex-shrink: 0;
}

.session-badge-input {
  display: inline-block;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.18);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.35);
  flex-shrink: 0;
}
```

---

### 3.3 Markdown-Aware Deduplication (`ARGON-V14-18`)

```javascript
function stripMarkdownSyntax(text) {
  if (!text) return '';
  return text
    .replace(/```[\s\S]*?```/g, '')     // Fenced code blocks
    .replace(/`([^`]+)`/g, '$1')         // Inline code
    .replace(/\*\*([^*]+)\*\*/g, '$1')   // Bold
    .replace(/\*([^*]+)\*/g, '$1')       // Italic
    .replace(/^#+\s+/gm, '')             // Headers
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // Links
    .replace(/\s+/g, ' ')
    .trim();
}

function computeSessionPreview(s) {
  const rawTitle = (s.title || '').trim();
  const rawPreview = (s.preview || '').trim();
  const cid = (s.conversation_id || '').trim();

  if (!rawPreview || rawPreview === cid) {
    return '';
  }

  const cleanTitle = stripMarkdownSyntax(rawTitle).toLowerCase();
  const cleanPreview = stripMarkdownSyntax(rawPreview).toLowerCase();

  // If preview matches title after stripping Markdown
  if (cleanPreview === cleanTitle) {
    return '';
  }

  // If preview starts with title
  if (cleanPreview.startsWith(cleanTitle)) {
    const remainder = stripMarkdownSyntax(rawPreview)
      .slice(cleanTitle.length)
      .replace(/^[:\s\-—]+/, '')
      .trim();
    return remainder.length > 5 ? remainder : '';
  }

  return stripMarkdownSyntax(rawPreview);
}
```
