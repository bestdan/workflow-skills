// Drives the page header's sync script under jsdom, for
// scripts/test_task_dashboard_server.py.
//
// Usage: node test_task_dashboard_header_dom.js <jsdom dir> <case.json>
//
// The case names the page, the /status answers in order (the last repeats),
// the POST /sync answer (and its status, `post_status`, 202 by default), and
// whether to click the sync icon. Timers are cut to a
// few milliseconds so the poll runs through every answer. Prints the header's
// state as each /status answer is about to be read (so `seen[i]` is the state
// the answer before it left), then once more at the end, as JSON.
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require(path.join(process.argv[2], "jsdom"));

const spec = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const errors = [];
let reloaded = false;
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (/navigation|reload/i.test(String(e.message))) reloaded = true;
  else errors.push(String(e.message));
});

const seen = [];
let afterPost = null; // its visible text just after the POST /sync answer
const calls = [];
let doc = null;
let next = 0;

function snap() {
  const btn = doc.getElementById("sync");
  const note = doc.getElementById("sync-note");
  const fresh = doc.getElementById("new-data");
  return {
    // The sync icon's state is its accessible name.
    button: btn.getAttribute("aria-label"),
    disabled: btn.disabled,
    note: note.hidden ? null : note.textContent,
    new_data: !fresh.hidden,
  };
}

function answer(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const dom = new JSDOM(spec.page, {
  url: "http://127.0.0.1:8765/",
  runScripts: "dangerously",
  virtualConsole: vc,
  pretendToBeVisual: true, // else document.hidden, and the poll waits
  beforeParse(window) {
    const realTimeout = window.setTimeout.bind(window);
    window.setTimeout = (fn, ms) => realTimeout(fn, Math.min(ms || 0, 5));
    window.fetch = (url, opts) => {
      const method = (opts && opts.method) || "GET";
      calls.push({ url: String(url), method });
      if (method === "POST") {
        const p = answer(spec.post_status || 202, spec.post);
        // Read the button right after the page's own handler has taken the
        // answer: this chain's second step queues behind that handler, and a
        // microtask runs before any poll timer can clear the text.
        p.then(() => {}).then(() => {
          afterPost = doc.getElementById("sync").textContent;
        });
        return p;
      }
      seen.push(snap());
      const s = spec.statuses[Math.min(next, spec.statuses.length - 1)];
      next++;
      return answer(200, s);
    };
  },
});
const { window } = dom;
doc = window.document;
// How many times the sync icon's node was replaced: a redraw restarts its spin.
let iconSwaps = 0;
new window.MutationObserver((records) => {
  for (const r of records) {
    for (const n of r.removedNodes) if (n.classList && n.classList.contains("glyph")) iconSwaps++;
  }
}).observe(doc.getElementById("sync"), { childList: true });

const settle = () => new Promise((r) => window.setTimeout(r, 30));

(async () => {
  if (spec.click) {
    doc.getElementById("sync").dispatchEvent(
      new window.MouseEvent("click", { bubbles: true }),
    );
  }
  for (let i = 0; i < 200 && next <= spec.statuses.length; i++) await settle();
  await settle();
  const final = snap();
  process.stdout.write(
    JSON.stringify({
      seen,
      final,
      calls,
      reloaded,
      errors,
      icon_swaps: iconSwaps,
      after_post: afterPost,
      final_text: doc.getElementById("sync").textContent,
    }),
  );
  window.close();
})();
