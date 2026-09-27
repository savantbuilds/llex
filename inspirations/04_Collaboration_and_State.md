# Server Integration and State Management

**Author:** Staff Backend & State Engineer
**Focus:** CRDT Sync, Liveblocks, Convex, Zustand

## 1. Convex and Liveblocks (The Network Layer)
The source project is a masterclass in modern WebSockets/CRDT integration built on **Liveblocks** and **Convex**.
*   **Data Model (Convex):** The database persistently stores highly structured, strongly typed document metadata (	itle, ownerId, organizationId).
*   **The Liveblocks Engine:** Tiptap is natively integrated with @liveblocks/react-tiptap, an advanced CRDT library leveraging Y.js underneath. Native @tiptap/extension-collaboration-cursor manages remote multi-user overlapping cursors asynchronously over WebSockets.

### State Container (Zustand)
The source uses use-editor-store.ts (zustand) to centrally manage exactly one complex piece of context: *The Tiptap Editor Instance*.
Because Tiptap initializes asynchronously deep within the React component tree (Editor), elements higher up in the DOM structure (like the Ribbon Toolbar or the top Navbar) need direct programmatic access to editor.chain().focus().toggleBold(). Passing props vertically is impossible, so Zustand acts as a global singleton repository for the editor object.

## 2. Backend Translation for LLex (FastAPI)
The translation of global cloud data synchronization to local desktop logic requires structural simplification.

### Replicating Zustand in Vanilla JS
In our editor.js script, the entire DOM space rests at the same initialization context level as the Javascript event loop. We absolutely have no need for Zustand and can store the editor variable globally in memory:
\\\javascript
let editorInstance = null; // Our "Zustand"
const editor = new Editor({...});
editorInstance = editor;
// Access it instantly anywhere in editor.js or export it to window.editor
\\\

### Collaboration & CRDT Extraction
Our local PyWebview text editor fundamentally **does not require Y.js or CRDTs** unless we explicitly build multi-user network sharing right now. Tiptap functions perfectly as an isolated Single-User Editor without @tiptap/extension-collaboration. 
**Our Approach:** 
*   We drop Liveblocks immediately.
*   We use TipTap's standard editor.getHTML() or editor.getJSON() to extract the raw ProseMirror object tree representing the document.
*   FastAPI assumes the role of local disk storage. 

### Bridging the Backend (FastAPI Endpoint Construction)
Rather than executing complex GraphQL-style React Queries directly into Convex (pi.documents.getById), we simply use raw asynchronous fetch APIs in editor.js:
\\\javascript
async function loadDocument() {
    const raw = await fetch('/api/document/load');
    const json = await raw.json();
    editor.commands.setContent(json.content);
}

async function saveDocument() {
    const content = editor.getJSON();
    await fetch('/api/document/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ content })
    });
}
\\\
This seamlessly marries our existing LocalLLMBridge and File Dialog functionality previously rooted in the PyQt architecture.

### The Real-Time LLM Need
If we need to stream token-by-token generation from a local LLM directly into the cursor context (which PyQt6 did gracefully with synchronous thread signals), we will establish a single local WebSocket on the FastAPI instance. As LLM tokens arrive from Python, they are pushed over the WebSocket and immediately injected via editor.commands.insertContent(token).