# User Interface & Interaction Patterns

**Author:** Sr. UX/UI Engineer
**Focus:** Web Typography, Interactive Layouts, TailwindCSS, Headless UI

## 1. UX Pattern Analysis
The target repo meticulously reconstructs the classic Google Docs visual layout using modern web patterns. The hierarchy is cleanly divided:
*   **Header / Navbar (App Layer):** File operations, Document Name, Share buttons.
*   **Toolbar / Ribbon (Format Layer):** Contextual formatting buttons (Bold, Align, Lists).
*   **Ruler (Structure Layer):** A complex, interactive React element that adjusts margins.
*   **Canvas (Content Layer):** A centered infinite-scroll container hosting the 816px wide paper element.

### Headless Components (shadcn/ui + Radix)
They utilize shadcn/ui extensively (src/components/ui/*.tsx), relying on Radix UI primitives. 
*   **Why?** Shadcn/ui gives them unstyled, accessible React components (Dropdowns, Dialogs, Selects) that they stylize freely with Tailwind CSS to match Google's Material-lite look.
*   **Ribbon Mechanics:** Radix's Menubar and DropdownMenu are used heavily in the navigation to build complex cascading context menus.

### The Interactive Ruler
The ruler (uler.tsx) is a technical highlight. It implements native DOM mouse tracking (onMouseMove, onMouseUp) to update Liveblocks storage variables bounding the paddingLeft and paddingRight of the Tiptap editor dynamically. 

## 2. Translation to LLex (Vanilla Web)
We do not have React, Tailwind, or Shadcn in our PyWebview architecture.

### Reproducing Shadcn / Tailwind with CSS
*   **Tailwind:** We will extract the core Tailwind sizing (paddings, colors, flexbox concepts) directly into our styles.css. Since we control the HTML directly, writing semantic CSS variables for root theme colors is fully sufficient to accomplish the visual aesthetic of the Google Docs interface.
*   **Shadcn/Radix:** This is our primary UI translation challenge. Building cascading context menus, dropdown pickers for font-size, and complex accessible dialogs is difficult in Vanilla JS. 
*   **Our Approach:** For native desktop menus, we can rely on standard <select> inputs initially or use lightweight Vanilla UI libraries if necessary. Given the constraints of PyWebview, keeping the DOM extremely simple initially (standard HTML buttons representing the Ribbon) allows us to replicate the exact UX layout but with significantly lower bundle size.

### Recreating the Ruler
Translating the Ruler to LLex is highly feasible but requires a sophisticated vanilla JS event loop to capture mousedown on the ruler nodes, calculate X offsets relative to the .editor-scroll-container, and imperatively apply inline styles (editor.view.dom.style.paddingLeft = X + 'px').

### Sidebar Integration (The LLM Interface)
The source project does not natively feature a persistent sidebar like ours. 
We must explicitly carve out a CSS Grid layout ensuring our right-hand side#sidebar sits beside the .editor-scroll-container without clipping or resizing the strict 816px paper width, unlike responsive web design where the center column usually compresses.