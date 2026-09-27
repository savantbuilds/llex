# Architecture Overview: Davronov-Alimardon/google-docs

**Author:** Staff Systems Architect
**Focus:** High-Level Topology, Client-Server Communication, Desktop Translation

## 1. Source Architecture Analysis
The analyzed target repository is a modern web application built on the **Next.js 15 App Router** ecosystem. Its primary design goal is real-time collaborative document editing. 

### Core Components:
- **Hosting & SSR:** Next.js handling both Static Site Generation and server-side routes.
- **Database / Backend:** Convex handles persistent document storage, user metadata, and active document state.
- **Collaboration Engine:** Liveblocks provides the WebSockets infrastructure. It manages Presence (who is online, where their cursor is) and Document sync (CRDTs via Y.js).
- **Authentication:** Clerk manages secure OAuth and JWT issuance.

## 2. Desktop Translation Strategy (LLex)
Our destination stack is significantly different. We are building a local-first desktop application encapsulated in a webview, not a globally distributed SaaS.

### Next.js vs. PyWebview + FastAPI
The source leverages Next.js primarily for its routing, SSR, and built-in API routes connecting to Convex.
**Our Approach:** 
We've completely decoupled the frontend from backend routing using **PyWebview**. FastAPI acts as our local daemon substituting Next.js API routes and Convex. Because we are locally hosted, we do not need SSR. We can serve raw static HTML/JS/CSS assets straight from the FastAPI mount point.

### WebSockets & Liveblocks vs. Local Persistence
The source dedicates immense complexity to Liveblocks for real-time Y.js CRDT synchronization. 
**Our Approach:** 
Since LLex is currently single-user desktop software, we can completely strip out Liveblocks, Y.js, and Clerk. Fast local etch() calls or native PyWebview JS bindings to our Python backend will handle save/load operations instantaneously. If collaborative editing is added later, FastAPI's WebSocket routers provide a native path for integrating Y.js.

## 3. Key Takeaways for LLex
* **Decoupled Editor Context:** The source separates the Editor completely from the Room and Navbar. We should ensure our HTML layout isolates the editor-container from the sidebar and ibbon to prevent CSS cascading issues.
* **Stateless UI:** The source uses React's state to bind the Ribbon UI to Tiptap's state. We will map this to Vanilla JS Event Listeners in editor.js.