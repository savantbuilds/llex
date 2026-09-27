# Editor Core Implementation: Tiptap Configuration

**Author:** Staff Frontend Editor Specialist
**Focus:** Lexical Parsing, Pagination, Tiptap Extensions

## 1. Deep Dive into Tiptap Usage
The source repository revolves around @tiptap/react and a large assortment of Tiptap plugins. This is highly advantageous for our migration because Tiptap is inherently framework-agnostic. They wrap it in React (useEditor), but the core engine runs entirely on ProseMirror logic.

### Structural Editor Dimensions
One of our biggest hurdles was implementing physical page breaks and "real" paper UI sizes in a continuous web view.
The source repository completely solves this the same way we architected it:
\\\	s
class: "focus:outline-none print:boder-0 border bg-white border-editor-border flex flex-col min-h-[1054px] w-[816px] pt-10 pr-14 pb-10 cursor-text"
\\\
**Analysis:** They use Tailwind CSS to constrain the editor container strictly to a width of 816px and a minimum height of 1054px (roughly 8.5 x 11 inches at standard web DPI). Notice they use min-h instead of h. They DO NOT force discrete multi-page DOM splits. If the document exceeds one page, the white container simply grows vertically. This is known as "Pageless" or "Draft" view.

### Custom Tiptap Extensions
The repository uses several custom extensions natively built into the src/extensions directory to expand ProseMirror's capabilities:
* **Line-Height:** A heavily customized extension interacting heavily with <p> padding.
* **Font-Size:** ProseMirror doesn't easily store arbitrary style strings without explicitly allowing them in the schema.
* **Image Resize:** @tiptap/extension-image combined natively with 	iptap-extension-resize-image.
* **Margins:** A top-level Ruler component (uler.tsx) injects live CSS variables (padding-left: Xpx) directly into the Tiptap root element attributes.

## 2. Translation to LLex Vanilla JS
Because we are discarding React (@tiptap/react), we rely entirely on @tiptap/core and the native DOM constructor (
ew Editor({})).

### Handling the Extensions
The source relies heavily on React for UI dropdowns mapping to these extensions (like Font Family selector). 
**Our Approach:**
We will define our own UI buttons inside index.html (e.g., <button id="btn-family">). In editor.js, we use editor.chain().focus().setFontFamily('Inter').run() explicitly attached to ddEventListener('click').

### Implementing the Pseudo-Pagination
We map their Tailwind class exactly to our styles.css.
\\\css
.tiptap {
    width: 816px;
    min-height: 1056px;
    background-color: var(--paper-bg);
}
\\\
If we wish to add the visual dashed page break as previously designed, we can write an event listener in Vanilla JS that calculates contentHeight / 1056 and renders absolutley-positioned DOM lines over the editor-scroll-container as the document scrolls natively inside Tiptap.

### Key Takeaway for LLex Integration
We must ensure our ESM imports in editor.js load the extensions correctly. The source repository leverages 
pm install and Node resolution. We are using unbundled esm.sh CDNs for our dependencies. We need to be careful picking extensions (like Color, Underline, Table) that have valid CDN targets, or bundle them via Webpack/Vite later if the offline requirement grows strong enough.