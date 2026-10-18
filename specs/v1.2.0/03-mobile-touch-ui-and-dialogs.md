# Specification 03: Mobile Touch UI & Decision Dialogs

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.2.0` (Interactive Questions & Touch Decision Modals)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 15 / 16 / 17+)  
> **Branch**: `v1.2.0`  
> **Review Status**: **ARCHITECTURALLY AUDITED & HARDENED BY GEMINI 4 ARGON (100% Mitigations Integrated)**

---

## 1. Overview & Mobile Touch Ergonomics

On a handheld Android smartphone screen, interactive prompts must be easy to tap with one thumb, clearly formatted, and prevent accidental dismissals. 

This specification establishes the UI/UX architecture for the **Interactive Question Modal** (`#modalAskQuestion`) rendered in [`agy-ui/index.html`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/agy-ui/index.html).

### Design Principles:
1. **Bottom Sheet Anchor**: Slides up smoothly from the bottom of the viewport (`transform: translateY(...)`), placing interactive targets within comfortable thumb reach.
2. **Card-Style Selection Targets**: Options are rendered as large, full-width touchable cards ($\ge 48\text{px}$ touch target height) with active state borders and subtle background fills.
3. **Recommendation Badges**: Options beginning with `(Recommended)` are automatically styled with an accent badge (`badge-rec`), highlighting optimal defaults.
4. **Unified Scroll Container (`M-09`)**: Both option cards and the custom write-in field reside within a unified `.question-body.scrollable` container with dynamic `max-height: 85dvh`. This prevents the Android soft keyboard from crushing options or occluding the input.
5. **Edge-to-Edge Navigation Bar Insets (`M-12`)**: `.question-actions` incorporates `calc(12px + env(safe-area-inset-bottom, 0px))` padding, preventing overlap with Android 15+ gesture navigation pills.
6. **Zero Accidental Dismissals**: Tapping outside the sheet (on the dimmed backdrop) does NOT dismiss the dialog without confirmation. Users must deliberately tap **Submit** or **Skip**.
7. **Back Button Safety**: Back gestures are intercepted via `window.handleAndroidBack()` to dismiss the question with an explicit "Skip" result rather than popping out of the app.

---

## 2. Dialog Component Hierarchy (`#modalAskQuestion`)

```html
<section class="modal-overlay hidden" id="modalAskQuestion">
  <div class="bottom-sheet-content question-sheet">
    
    <!-- Drag Handle & Sticky Header -->
    <div class="sheet-handle"></div>
    <header class="question-header">
      <div class="question-meta">
        <span class="question-badge" id="questionStepBadge">Decision Required</span>
        <span class="question-counter" id="questionCounter">1 of 1</span>
      </div>
      <h3 class="question-title" id="questionPromptText">Question Title...</h3>
      <p class="question-subtitle" id="questionSubtitle">Select an option to proceed</p>
    </header>

    <!-- Unified Scrollable Container (M-09: Options + Write-in) -->
    <div class="question-body scrollable" id="questionBodyScrollable">
      
      <!-- Dynamically populated option cards -->
      <div class="question-options-container" id="questionOptionsContainer">
        <!-- Rendered option cards -->
      </div>

      <!-- Custom Write-in Field inside scroll body to avoid IME keyboard occlusion -->
      <div class="question-writein-container">
        <div class="writein-toggle-bar" id="btnToggleWriteIn">
          <i class="fa-solid fa-pen-to-square"></i>
          <span>Or type custom response...</span>
        </div>
        <div class="writein-input-wrapper hidden" id="writeInWrapper">
          <textarea id="questionWriteInInput" rows="2" 
                    placeholder="Enter custom instructions or alternative choice..."></textarea>
        </div>
      </div>

    </div>

    <!-- Fixed Bottom Actions Bar with SDK 35 Safe-Area Insets (M-12) -->
    <footer class="question-actions" id="questionActions">
      <button class="btn btn-secondary" id="btnQuestionSkip">Skip</button>
      <button class="btn btn-primary" id="btnQuestionSubmit" disabled>Submit</button>
    </footer>

  </div>
</section>
```

---

## 3. UI State Management & Interaction Rules

### A. Selection Modes
1. **Single-Select (`is_multi_select: false`)**:
   - Option cards behave as radio buttons.
   - Tapping an unselected card selects it and deselects all other cards.
   - Enables the **Submit** button immediately.
2. **Multi-Select (`is_multi_select: true`)**:
   - Option cards behave as checkboxes.
   - Multiple cards can be toggled simultaneously.
   - Displays a checkbox indicator with smooth checkmark fill.
   - Enables **Submit** if at least one card is selected OR if the custom write-in field has content.

### B. Recommendation Parsing
When populating cards, the renderer checks for the prefix `(Recommended)`:
- Strips `(Recommended)` only from the visible card label.
- Preserves the full original string in `data-value` (ensuring the answer returned to the LLM matches its exact expectation).
- Injects a compact `<span class="badge-rec">Recommended</span>` pill above the option label.
- Renders the card with an accent tint border (`var(--accent-primary-alpha)`).

### C. Multi-Question Wizard Flow (`M-13`)
When the payload contains multiple questions (`questions.length > 1`):
1. `questionCounter` displays `"Question X of Y"`.
2. For steps before the last (`stepIndex < questions.length - 1`), `#btnQuestionSubmit` displays `"Next"`.
3. Tapping `"Next"` validates that a selection or write-in has been provided for the current question, saves it to an in-memory `collectedResponses` array, and re-renders the dialog for the next question with a smooth horizontal fade.
4. On the final question (`stepIndex === questions.length - 1`), `#btnQuestionSubmit` displays `"Submit"`. Tapping `"Submit"` dispatches the full structured payload:
   ```json
   {
     "question_id": activeQuestionId,
     "skipped": false,
     "responses": collectedResponses
   }
   ```

### D. Custom Write-In Expansion & Keyboard Physics (`M-09`)
- By default, the write-in input is collapsed behind: `Or type custom response...`.
- Tapping the bar smoothly expands the textarea and focuses the input.
- On focus, `questionWriteInInput.scrollIntoView({ behavior: 'smooth', block: 'nearest' })` is invoked with a 150ms delay, ensuring Android's soft keyboard does not cover the input.
- Typing in the textarea automatically enables the Submit button even if no predefined option card is selected.
- If an option card IS selected AND write-in text is typed, the backend formats the response as:
  `A1: <Selected Option> (User Note: <Write-In>)`.

### E. Reconnect Rehydration & Deduplication (`M-14`)
- The frontend stores `activeQuestionId = null`.
- When an `ask_question` frame or rehydration response from `GET /api/question/pending` arrives:
  - If `activeQuestionId === event.question_id`, the event is ignored as a no-op to preserve in-progress user selections and active textarea typing.
  - If `activeQuestionId !== event.question_id`, the modal is initialized with the new question.
- On WebSocket reconnect, `GET /api/question/pending` is queried automatically.

---

## 4. Android Back Button Interception

In [`app/src/main/java/ai/agy/app/MainActivity.kt`](file:///data/data/com.termux/files/home/storage/shared/Documents/AgyStudio/app/src/main/java/ai/agy/app/MainActivity.kt), back gestures trigger `window.handleAndroidBack()`:

```javascript
window.handleAndroidBack = function() {
  const qModal = document.getElementById('modalAskQuestion');
  if (qModal && !qModal.classList.contains('hidden')) {
    // Treat back button as deliberate "Skip" action
    submitQuestionResponse({ skipped: true });
    return true; // Back event consumed
  }
  return false;
};
```

---

## 5. CSS & Motion Specifications

```css
/* Bottom sheet slide-up animation with dynamic viewport awareness */
.question-sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  max-height: 85dvh;
  border-top-left-radius: 20px;
  border-top-right-radius: 20px;
  background: var(--bg-surface-elevated, #1a1e28);
  box-shadow: 0 -8px 32px rgba(0, 0, 0, 0.45);
  display: flex;
  flex-direction: column;
  transform: translateY(100%);
  transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  z-index: 1050;
}

.modal-overlay.active .question-sheet {
  transform: translateY(0);
}

/* Unified Scroll Container (M-09) */
.question-body.scrollable {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 12px 16px;
  -webkit-overflow-scrolling: touch;
}

/* Option Card Typography & Touch Heights */
.option-card {
  min-height: 52px;
  display: flex;
  align-items: center;
  padding: 12px 16px;
  margin-bottom: 8px;
  border-radius: 12px;
  background: var(--bg-card, #222736);
  border: 1.5px solid transparent;
  cursor: pointer;
  touch-action: manipulation;
}

.option-card.selected {
  border-color: var(--accent-primary, #6366f1);
  background: var(--accent-bg-subtle, rgba(99, 102, 241, 0.12));
}

.badge-rec {
  background: var(--accent-primary, #6366f1);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
  margin-right: 6px;
}

/* Android 15/16 SDK 35 Safe-Area Inset Bottom Actions (M-12) */
.question-actions {
  display: flex;
  gap: 12px;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom, 0px)) 16px;
  border-top: 1px solid var(--border-subtle, rgba(255, 255, 255, 0.08));
  background: var(--bg-surface-elevated, #1a1e28);
}
```
