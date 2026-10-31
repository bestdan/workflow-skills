// Drives the page's Refresh button under jsdom, for
// scripts/test_task_dashboard_refresh.py.
//
// Usage: node test_task_dashboard_refresh_dom.js <jsdom dir> <case.json>
//
// The case names the page before and after the refresh, the POST /refresh
// answer, the card to click, and edits to make first. Cards have no editor
// of their own any more, so each edited card is given a "why not" line like
// Now's: `open` cards get it shown with text typed, `dirty` cards get text
// typed with it hidden. fetch is stubbed: the POST answers `result`, the page
// fetch answers `after`. Prints the page's state afterwards as JSON.
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
const dom = new JSDOM(spec.before, {
  url: "http://127.0.0.1:8765/prs",
  runScripts: "dangerously",
  virtualConsole: vc,
});
const { window } = dom;
const doc = window.document;
const calls = [];

function answer(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(body),
  });
}
window.fetch = (url, opts) => {
  calls.push({ url: String(url), method: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") return answer(200, JSON.stringify(spec.result));
  return answer(200, spec.after);
};

function card(key) {
  return Array.from(doc.querySelectorAll(".card[data-key]")).find(
    (c) => c.dataset.key === key,
  );
}

const edited = {};
for (const [mode, keys] of [["open", spec.open || []], ["dirty", spec.dirty || []]]) {
  for (const key of keys) {
    const c = card(key);
    const line = doc.createElement("div");
    line.className = "whynot";
    line.hidden = mode !== "open";
    const area = doc.createElement("input");
    line.appendChild(area);
    c.appendChild(line);
    area.value = `unsaved ${mode} ${key}`;
    edited[key] = { card: c, area, text: area.value };
  }
}

const settle = () => new Promise((r) => window.setTimeout(r, 50));

(async () => {
  card(spec.click).querySelector("[data-refresh]").dispatchEvent(
    new window.MouseEvent("click", { bubbles: true }),
  );
  for (let i = 0; i < 40 && doc.querySelector(".card.refreshing"); i++) await settle();
  await settle();
  const lanes = {};
  doc.querySelectorAll(".lane[data-section]").forEach((l) => {
    lanes[l.dataset.section] = {
      heading: (l.querySelector("h2") || {}).textContent || "",
      keys: Array.from(l.querySelectorAll(".card[data-key]")).map((c) => c.dataset.key),
      stale: !!l.querySelector(".refresh-stale"),
      note: (l.querySelector(".refresh-note") || {}).textContent || "",
    };
  });
  const edits = {};
  for (const [key, e] of Object.entries(edited)) {
    edits[key] = {
      same_card: e.card.isConnected,
      text: e.area.isConnected ? e.area.value : null,
      kept: e.area.isConnected && e.area.value === e.text,
      open: !e.card.querySelector(".whynot").hidden,
    };
  }
  const failed = doc.querySelector(".refresh-error");
  process.stdout.write(JSON.stringify({
    lanes,
    edits,
    calls,
    reloaded,
    errors,
    failed: failed ? failed.textContent : null,
  }));
  window.close();
})();
