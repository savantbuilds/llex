# Phase 1: Core System Architecture Blueprint (Pre-LLM)

**Document Purpose:** This blueprint serves as the definitive engineering and architectural guide for constructing the foundational word processor. It strictly outlines the component design, core principles, and build sequence necessary to ship a stable, student-ready, deterministic writing environment *before* any artificial intelligence or LLM features are integrated.

## 1. HIGH-LEVEL GOAL

The purpose of this phase is to build a rock-solid, student-ready word processing environment. Before we introduce any generative AI functionality, the editor must be completely deterministic, stable, and capable of replacing standard academic writing tools (e.g., Google Docs, MS Word) for day-to-step tasks. 

If the basic text formatting, pagination, navigation, or save/load states are brittle, AI integrations will only amplify the chaos. The goal is 100% fidelity in structural editing, offline-capable saving, and visual layout predictability.

---

## 2. CORE PRINCIPLE

**The editor is a system of state-driven, composable components.** 

We do not treat the word processor as a giant HTML `contenteditable` canvas. Instead, we treat it as a strict data structure (JSON state tree) managed by a central engine (ProseMirror/Tiptap). 

* **State is Truth:** The DOM is merely a visual reflection of the Editor State. 
* **Transactions:** Any change (user typing, pasting, clicking "Bold") is a transactional update applied to the state, never a direct DOM manipulation.
* **Composability (Lego Pieces):** Every feature (Bold, Heading, Page, Outline) is a perfectly isolated module (Extension/Node/Mark) that plugs into the core engine.

---

## 3. COMPONENT BREAKDOWN (THE LEGO PIECES)

### 1. Central Editor Engine
* **Responsibility:** Maintain the document state tree, manage history (undo/redo), and broadcast state changes (`onUpdate`, `onSelectionUpdate`).
* **Inputs:** Keyboard events, Toolbar command transactions, Paste events.
* **Outputs:** Rendered DOM, JSON/HTML serialization.
* **Dependencies:** None.
* **Why it is necessary:** Without a strict state manager, raw `contenteditable` degrades into broken HTML tags and layout bugs.

### 2. Strict Pagination Controller (Node Extensions)
* **Responsibility:** Enforce rigid 8.5" x 11" visual and structural page boundaries. Automatically flow overflowing blocks into new pages.
* **Inputs:** Editor `onUpdate` cycle (checking DOM height vs Node size).
* **Outputs:** Sliced and redistributed Tiptap Nodes mapping to `<div class="page">`.
* **Dependencies:** Central Editor Engine.
* **Why it is necessary:** Students require standard formatting representations for printed or exported assignments.

### 3. I/O Storage Bridge
* **Responsibility:** Serialize the Editor State to HTML/JSON, transit it to the local Python backend (FastAPI), and deserialize safely upon load.
* **Inputs:** JSON/HTML strings from Editor Engine.
* **Outputs:** File system writes (`.llex` files or standard `.html`).
* **Dependencies:** Central Editor Engine.
* **Why it is necessary:** An editor is useless if work cannot be reliably saved to the disk and perfectly restored without layout degradation.

### 4. Formatting Command UI (Ribbon & Context Menu)
* **Responsibility:** Translate user intents (clicks) into ProseMirror transactions (e.g., `toggleBold()`, `setHeading(level: 2)`). Read active editor state to highlight active buttons.
* **Inputs:** Mouse clicks, Editor `onSelectionUpdate` events.
* **Outputs:** Dispatch transactions to the Central Editor Engine.
* **Dependencies:** Central Editor Engine.
* **Why it is necessary:** Provides the physical interface for users to structure text without hotkeys.

### 5. Document Outline Navigator
* **Responsibility:** Crawl the editor's node tree to extract semantic `<h>` tags and generate a clickable table of contents.
* **Inputs:** Editor `onUpdate` events.
* **Outputs:** Appended DOM elements in the lateral Sidebar.
* **Dependencies:** Formatting Command UI (needs Headings to exist).
* **Why it is necessary:** Academic documents require deep structural navigation. Headings must act as structural metadata, not just large text.

---

## 4. BUILD ORDER (CRITICAL PATH)

1. **Central Editor Engine (`editor.js` & TipTap Setup):** *Prerequisite for everything.* We must establish the base `Editor` instance handling standard paragraphs and marks.
2. **I/O Storage Bridge (`api.py` & Load/Save events):** *Blocks reliability testing.* Once we can write text, we must prove we can save and parse it before adding complex nodes.
3. **Strict Pagination Controller (`Page` & `CustomDocument` nodes):** *Blocks layout.* We must shift the infinite-scroll canvas into rigid `.page` wrappers and build the paste-sanitization and overflow transaction loop.
4. **Formatting Command UI (`index.html` & `mini-toolbar.css`):** Connect the buttons to the established engine to allow rich text capabilities.
5. **Document Outline Navigator:** Build the semantic tree walker once headers are reliably formatting via the UI.

*Why this order matters:* You cannot paginate a document that isn't cleanly loading from disk. You cannot build a semantic outline if you have no way to apply Headings. Foundational data beats visual UI.

---

## 5. INTERACTION LAYER

The system relies on a **Unidirectional Data Flow**:

1. **User Action:** User clicks "Bold" in the native UI.
2. **Command Dispatch:** The UI triggers `editor.chain().focus().toggleBold().run()`.
3. **State Mutation:** The Editor Engine intercepts the command, mutates the JSON state tree (adding a `strong` mark to the text range), and emits an `onUpdate` transaction.
4. **Render & Reflow:** The Editor updates the target DOM node. Simultaneously, the Pagination Controller listens to `onUpdate`, checks if the new bold text caused the block to overflow the physical page limits, and if so, dispatches *another* transaction to split the page. 
5. **UI Sync:** The Editor fires `onSelectionUpdate`, which the UI reads to physically highlight the "B" button in the menu.

---

## 6. MINIMUM COMPLETE SYSTEM DEFINITION

The "v1 Complete" Student Word Processor must achieve the following without error:

* Appears visually identical to an 8.5x11 MS Word / Google Docs print layout environment.
* Can type continuously, automatically flowing text into a mathematically new `.page` block when height is exceeded.
* Can copy massive external text blocks and paste them safely (stripping malicious absolute CSS or nested page wrappers).
* Can select text and accurately apply Bold, Italic, Underline, and Headings 1-6.
* Generates a live, clickable Document Outline in the sidebar.
* Can click "Save" and natively write an `.llex` file to the user's OS via the local Python API.
* Can close the app, open the file, and restore 100% of the text, structure, and page breaks perfectly.

---

## 7. EXPLICITLY EXCLUDE LLM FEATURES

Under no circumstances should the initial v1 architecture perform the following:
* **No `execute_scaffolds` logic.** UI scaffolds or AI prompt overlays must be completely suppressed or mocked.
* **No backend API bridges to LLMs.** `llm.py` logic targeting generative endpoints should remain unlinked. 
* **No generative text generation.** 

**Why:** The standard word processing framework must be tested in isolation. LLM outputs are highly dynamic and will break pagination or layout calculations if the foundational `onUpdate` flow is not battle-hardened first. We prepare the UI space (buttons exist but do nothing) so LLMs can simply hook into stable pipeline events safely later.