// Drives a Now card's Snooze, reason and Undo under jsdom, for
// scripts/test_task_dashboard_whynot.py.
//
// Usage: node test_task_dashboard_whynot_dom.js <jsdom dir> <case.json>
//
// The case names the page, the card to act on (`key`), the `steps` to take
// in order ("snooze", "undo", "for:<days>" or "reason:<code>"), optional `text` typed into
// the line before a reason, and the `status` every POST answers (200 when
// absent). fetch is stubbed and records each POST's path and body, for the
// test to replay against a real server. Prints, as JSON, the calls and the
// card's state after each step.
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
const dom = new JSDOM(spec.page, {
  url: "http://127.0.0.1:8765/now",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;
const doc = window.document;
const calls = [];
const alerts = [];
window.alert = (m) => alerts.push(String(m));
window.fetch = (url, opts) => {
  calls.push({ url: String(url), method: (opts && opts.method) || "GET", body: opts && opts.body });
  const status = spec.status || 200;
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve("refused"),
  });
};

const card = Array.from(doc.querySelectorAll(".card[data-key]")).find(
  (c) => c.dataset.key === spec.key,
);
const line = card.querySelector(".whynot");
const title = card.firstElementChild;

function state() {
  return {
    connected: card.isConnected,
    title_shown: card.isConnected && window.getComputedStyle(title).display !== "none",
    line_shown: card.isConnected && !line.hidden,
    line_text: line.textContent,
    pressed: Array.from(line.querySelectorAll("[aria-pressed=\"true\"]")).map(
      (b) => b.textContent,
    ),
    cards: doc.querySelectorAll(".card[data-key]").length,
  };
}

function click(el) {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
}

const settle = () => new Promise((r) => window.setTimeout(r, 30));

(async () => {
  const states = [state()];
  for (const step of spec.steps) {
    if (step === "snooze") click(card.querySelector(".ov [data-ov=\"snooze\"]"));
    else if (step === "undo") click(line.querySelector("[data-ov=\"undo\"]"));
    else if (step.startsWith("for:")) {
      const days = `${step.split(":")[1]}d`;
      click(
        Array.from(line.querySelectorAll("[data-ov=\"snooze-for\"]")).find(
          (b) => b.textContent === days,
        ),
      );
    } else {
      line.querySelector("input").value = spec.text || "";
      click(line.querySelector(`[data-reason="${step.split(":")[1]}"]`));
    }
    await settle();
    states.push(state());
  }
  process.stdout.write(JSON.stringify({ states, calls, reloaded, alerts, errors }));
  window.close();
})();
