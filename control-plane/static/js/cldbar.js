// cldBar — drop-in embed of remdev's Claude status-bar service.
// Copy this file into your app's static JS (or run ./install.sh <dir>),
// load it as a classic script, then:
//
//   <script src="/static/js/cldbar.js"></script>
//   <script>cldBar.mountCldBar(document.getElementById("footer-slot"),
//                              { theme: "vscode-dark" });</script>
//
// The iframe src is built in the BROWSER on purpose: an iframe URL is
// fetched by the viewer's machine, so a server-rendered 127.0.0.1 breaks
// for anyone not browsing on the station itself (see readme.md, "the
// 127.0.0.1 pitfall"). Default origin: the page's own hostname, port 6005.
// Apps pin an explicit origin via opts.url (config value — never hardcode).
(function (global) {
  "use strict";

  var DEFAULT_PORT = 6005;

  // Build the /statusbar URL. opts: url (explicit origin override),
  // port (default 6005), theme (slug, e.g. "vscode-dark"), compact
  // (default true — pass false for the taller ~32px bar with clock readouts).
  function cldBarUrl(opts) {
    opts = opts || {};
    var base;
    if (opts.url) {
      base = String(opts.url).replace(/\/+$/, "");
      if (!/^https?:\/\//.test(base)) {
        throw new Error("cldBar: opts.url must be http(s)://…, got " + base);
      }
    } else {
      base = global.location.protocol + "//" + global.location.hostname
        + ":" + (opts.port || DEFAULT_PORT);
    }
    var params = [];
    if (opts.theme) {
      // remdev accepts plain slugs only; unknown slugs fall back to garage
      if (!/^[a-z][a-z0-9-]{0,30}$/.test(opts.theme)) {
        throw new Error("cldBar: bad theme slug: " + opts.theme);
      }
      params.push("theme=" + opts.theme);
    }
    if (opts.compact !== false) params.push("compact=1");
    return base + "/statusbar" + (params.length ? "?" + params.join("&") : "");
  }

  // Create the iframe inside `mount` and return it. Extra opts: width
  // (CSS size, default "560px"), height (px number, default 22 compact /
  // 32 full), title, scheme ("light"|"dark" — defaults to "light" for
  // *-light themes, else "dark"; must match the embedded theme or the
  // browser drops transparency and paints an opaque canvas behind the
  // bar). If remdev is down the bar renders dimmed and self-recovers (60s
  // retry inside the iframe); if remdev is unreachable from the viewer's
  // machine the iframe is simply blank.
  function mountCldBar(mount, opts) {
    if (!mount || typeof mount.appendChild !== "function") {
      throw new Error("cldBar: mount must be a DOM element");
    }
    opts = opts || {};
    var frame = global.document.createElement("iframe");
    frame.className = "cldbar-embed";
    frame.src = cldBarUrl(opts);
    frame.title = opts.title || "Claude window status (remdev)";
    frame.setAttribute("scrolling", "no");
    frame.loading = "lazy";
    frame.style.border = "0";
    frame.style.height =
      (opts.height || (opts.compact !== false ? 22 : 32)) + "px";
    frame.style.width = opts.width || "560px";
    frame.style.maxWidth = "55vw";
    frame.style.verticalAlign = "middle";
    frame.style.colorScheme = opts.scheme
      || (opts.theme && opts.theme.indexOf("light") !== -1 ? "light" : "dark");
    mount.appendChild(frame);
    return frame;
  }

  global.cldBar = {
    mountCldBar: mountCldBar,
    cldBarUrl: cldBarUrl,
    DEFAULT_PORT: DEFAULT_PORT,
  };
})(window);
