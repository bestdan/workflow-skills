Symbols: ✅ pass / ran / done · 🟡 pass with suggestions · ⛔ blocking · ❌ failed · ⏱️ timed out · ➖ skipped / none / not applicable · ❓ needs your answer · ☐ check to run · 🔁 another round

## Overview

<✅ **pass** | 🟡 **pass with suggestions** | ⛔ **blocking**> — <N> fixes · <N> skipped · <N> calls · <N> checks
<one line of why, judged as the change will stand once the auto-fixes below are applied>

<reviewer> <✅ ran | ❌ failed | ⏱️ timed out | ➖ skipped — reason> · … · conventions <✅ attached | ➖ not attached — reason>

## Findings & verification

**Will fix (<N>)**

- ✅ <label> (<decoration>): <subject> — `<file:line>`

**Skipped (<N>)**

- ➖ <label> [(<decoration>)]: <subject> — <reason>

**Escalated (<N>)**

- <<label>: <subject> — independent reviewer: <recommendation> | ➖ none>

**Checks (<N>)**

- <☐ `<command or action>` · <where it runs> · pass = <what a pass looks like> · runs: <you | me> | ➖ none needed — <why>>

## Calls for you to make

<❓ <i> of <N> — <the highest-priority call as a single question>? (y/n) | ➖ none>

## Next round

**Fix commit:** <`sha` | ➖ none — nothing was applied | ➖ not applicable — `--post` changes no files>
**Recommendation:** <🔁 **another round** | ✅ **no further round** | ➖ **not applicable**> — <the reason, grounded in what the fixes changed: an interface or contract, a reconciler-authored fix, collateral edits, a test never shown to fail, or the round-over-round yield that says the review has converged>
