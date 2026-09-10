// resman — the Claude window/week status bar in the footer.
//
// resman is remdev's *data backend* for this bar (window model, schedule,
// claude.ai probe), but it embeds the bar like any other app: through
// remdev, never by rendering meters of its own.
//
// The bar itself is remdev's service, embedded through the shared cldBar
// kit (solBench/cldBar). static/js/cldbar.js is a *copy-in* of that kit —
// refresh it with `cldBar/install.sh <this dir>`, never edit it here. This
// file is resman's policy layer: where the bar mounts, which remdev origin
// it uses, and how the app's four themes map onto remdev's theme slugs.
"use strict";

// resman theme -> remdev theme slug (a token block in remdev's garage.css).
// Keep the keys in sync with THEMES in core.js: an unmapped theme falls back
// to the dark slug, which on Light Modern shows up as an opaque dark strip.
const CLDBAR_THEME = {
  dark: "vscode-dark",
  light: "vscode-light",
  hc: "vscode-hc",
  green: "vscode-green",
};

// The kit needs the theme slug and, only when the config pins one, an
// explicit remdev origin. With no pin it derives the origin from the page's
// own address — a server-rendered 127.0.0.1 would point every remote
// viewer at their own machine and blank the bar (cldBar readme, "the
// 127.0.0.1 pitfall").
function cldbarOptions() {
  const slot = $("#cldbar-slot");
  const pinned = slot && slot.dataset.remdevUrl;
  return {
    theme: CLDBAR_THEME[currentTheme()] || CLDBAR_THEME.dark,
    ...(pinned ? { url: pinned } : {}),
  };
}

function setupCldBar() {
  const slot = $("#cldbar-slot");
  if (!slot || !window.cldBar) return;   // kit not copied in — no footer bar
  try {
    cldBar.mountCldBar(slot, cldbarOptions());
  } catch (err) {
    // Only a malformed app.remdev_url gets here; the config validator
    // rejects those, so this is the belt to that braces.
    console.error("cldBar: not mounted —", err.message);
  }
}

// Re-point the embed when the user cycles the app theme (called from
// setTheme). The whole iframe is remounted rather than its src patched: the
// kit owns the color-scheme derivation, and an iframe whose color-scheme
// disagrees with the embedded page's loses transparency and paints an
// opaque canvas over the footer. Reloading the tiny page is cheap.
function syncCldBar() {
  const slot = $("#cldbar-slot");
  if (!slot || !window.cldBar) return;
  const frame = slot.querySelector("iframe.cldbar-embed");
  if (frame && frame.src === cldBar.cldBarUrl(cldbarOptions())) return;
  if (frame) frame.remove();
  setupCldBar();
}
