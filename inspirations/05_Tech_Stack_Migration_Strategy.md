# Technical Migration & Execution Strategy

**Author:** Technical Project Lead
**Focus:** Translating React/Liveblocks/Tailwind -> VanillaJS/FastAPI/PyWebview

## 1. Executive Summary of the Google Docs Clone
The target repository (Davronov-Alimardon/google-docs) is a phenomenal piece of React engineering demonstrating exactly how to build a cloud-native rich text editor using Tiptap, Liveblocks, and Tailwind. It solves two incredibly difficult problems elegantly:
1.  **Pageless Physical Layout:** By manipulating a min-h-[1054px] w-[816px] container, they render an infinitely extending document that feels like a physical paper page rather than splitting DOM nodes.
2.  **Deep UI Interactivity:** A unified React state (via Zustand and shadcn) orchestrates dynamic Ribbon interactions, margin rulers, and contextual menus.

## 2. Our Mission (LLex)
Our text editor, LLex, is a local-first desktop application wrapper (Python + FastAPI + PyWebview). We are moving _away_ from PySide6 UI due to cursor overlap and multi-widget layout bugs, and adopting Web UI.

The goal is strictly to emulate the **look, feel, and editor layout** of the cloned repository, but drastically simplify the **underlying technology stack** to match a single-user desktop architecture.

## 3. High-Level Translation Matrix
Below is the definitive map of how we strip out the cloud-native complexity and replace it with our lightweight desktop strategy:

| Feature / Domain | Target Repository (Google Docs Clone) | LLex Desktop equivalent (FastAPI + Vanilla) |
| :--- | :--- | :--- |
| **Framework** | Next.js 15 App Router | **PyWebview + FastAPI** serving static HTML/JS. |
| **Styling** | TailwindCSS + Shadcn/UI Component Library | **Raw CSS (styles.css)** replicating precise Tailwind utility dimensions explicitly. |
| **Editor Core** | @tiptap/react + useEditor | **@tiptap/core (Vanilla JS)** instantiated manually in <script type="module" src="editor.js">. |
| **State Mgt.** | Zustand (useEditorStore) | **Global JS Variables** exposed to the document window. |
| **Multiplayer** | Liveblocks + Y.js CRDTs | **None.** Immediate local execution; no OT/CRDT required. |
| **Database** | Convex Database | **Local File System** (JSON or HTML files on disk). |
| **Auth** | Clerk | **None.** Local desktop application user only. |
| **Layout Concept** | class="...min-h-[1054px] w-[816px]..." | Exact CSS replica: .tiptap { min-height: 1056px; width: 816px; } |
| **LLM Inference** | N/A | Local Python connection streaming outputs over WebSockets to Tiptap commands. |

## 4. Phased Execution Plan (Future Roadmap)

1.  **Phase 1: Basic Paper Replication (Done)**
    Build the static <div class="tiptap"> with drop-shadows and gray backgrounds replicating the basic paper aesthetic we learned from the editor.tsx component.
2.  **Phase 2: Ribbon & Tiptap Extensions**
    Map standard HTML <button> elements to Tiptap extension commands (	oggleBold, 	oggleHeading), removing React overhead. Write raw CSS replicating .btn:hover from Tailwind's utility sets.
3.  **Phase 3: Margin Rulers (Optional UX Polish)**
    Implement a Vanilla JS equivalent to their uler.tsx that binds mousemove events to update the .tiptap paddingLeft property natively if the user needs layout manipulation.
4.  **Phase 4: Save, Export, and LLM Binding**
    Build explicit FastAPI endpoints (/api/save, /api/load) connecting the Tiptap .getJSON() payload to Python's disk access, allowing users to save desktop .llex files natively within PyWebview, eliminating the need for Convex.

By understanding how they separated their Editor from the surrounding App and how they faked pagination physically, we have a concrete path to implementing their stellar UX without adopting any of their cloud backend burden.