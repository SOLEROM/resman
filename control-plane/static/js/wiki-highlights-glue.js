// wiki-highlights-glue.js — resman's end of the reader highlighter
// (wiki-marks.js, shared with interpreta/gridar/mentora).
//
// loadWiki() calls setupWikiHighlights(root, page) after rendering a page
// into #wiki-content, and with page = null before anything else replaces it
// (loading, search results, the favorites list, errors). A page the server
// calls highlightable gets the palette; anything else turns it off.
// app.js supplies the renderer (renderWikiMarkdown) and its description for
// the core (wikiMarksProfile); the POST goes to routes_highlights.py.
"use strict";

function setupWikiHighlights(root, page) {
  if (!root || !window.wikiMarks) return;
  // A <mark> in a page keeps one class at most, and only a highlight color.
  window.wikiMarks.cleanMarks(root);
  if (!page || !page.highlightable || !page.sha || !window.marked || !window.DOMPurify) {
    window.wikiMarks.detach(root);
    return;
  }
  const vault = state.selectedVault;
  window.wikiMarks.attach(root, {
    path: page.file,
    source: page.content || "",
    sha: page.sha,
    profile: wikiMarksProfile(),
    renderHtml: (markdown) => renderWikiMarkdown(markdown, { wikilinks: true }),
    save: (body) => api(`/api/vaults/${encodeURIComponent(vault)}/wiki/highlight`,
                        { method: "POST", body: JSON.stringify(body) }),
    onSaved: (fresh) => {
      const top = root.scrollTop;
      root.innerHTML = renderWikiMarkdown(fresh.content || "", { wikilinks: true });
      setupWikiHighlights(root, fresh);
      root.scrollTop = top;
    },
    reload: () => loadWiki(page.file, { fromHistory: true }),
  });
}
