// bench-signal — the app-side end of mainBench's frame signals.
// A copy-in kit file: install it with solBench/mainBench/embed/install.sh
// <app-static-js-dir>, load it as a classic script, never edit it in an app.
//
//   <script defer src="/static/js/bench-signal.js"></script>
//   ...
//   benchSignal.setupItem(document.getElementById("app-item"));
//
// Docked in the mainBench shell an app is a cross-origin iframe, so the only
// thing that reaches the shell is parent.postMessage. The shell
// (mainbench/static/js/frame-signal.js) accepts `{ bench: 1, type }` from the
// window of one of its own frames, still on the origin it was pointed at,
// and nothing else. One-way by design: this file never listens.
//
// targetOrigin is "*": an app must not carry the shell's address, and the
// payload holds nothing worth reading. Who may be the parent at all is the
// app's frame-ancestors header, not this file.
//
// The footer's app-name item: markup ships it as a plain label (no role, no
// tabindex, no title) — opened standalone there is no shell to ask, so it
// stays one. Inside a frame setupItem() makes it the button that asks the
// shell for its app bar, and marks it `.is-live` so CSS can keep pointer
// and hover for the live item only.
(function (global) {
  "use strict";

  var MARK = 1;                 // the protocol version (frame-signal.js SIGNAL_MARK)
  var HINT = "Show the bench app bar";

  // A top-level window is its own parent.
  function framed() {
    return global.parent !== global;
  }

  // Post one signal to the shell; false when there is no shell to ask.
  function send(type) {
    if (!framed()) return false;
    global.parent.postMessage({ bench: MARK, type: type }, "*");
    return true;
  }

  // opts.title: the app's own tooltip for the item, kept ahead of the hint.
  function setupItem(el, opts) {
    if (!el || !framed()) return false;
    if (el.classList.contains("is-live")) return true;   // wired once, asks once
    var ask = function () { send("toggle-strip"); };
    el.classList.add("is-live");
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.title = opts && opts.title ? opts.title + " · " + HINT : HINT;
    el.addEventListener("click", ask);
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ask(); }
    });
    return true;
  }

  global.benchSignal = { framed: framed, send: send, setupItem: setupItem, MARK: MARK };
})(window);
