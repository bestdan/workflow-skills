---
description: Re-research the coder capability matrix — re-check benchmark boards, pricing, model rosters, safety evaluations, and each vendor's terms, then write the results back into select-coder's matrix.md and bump its cache date. The occasional (~2-month) world-state refresh, distinct from /select-coder --refresh, which only re-probes which backends this machine can run
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, WebSearch, WebFetch
argument-hint: "[backend | model]"
---

# Refresh Coder Comparison

Follow the procedure in
[`skills/select-coder/refresh-coder-comparison.md`](../skills/select-coder/refresh-coder-comparison.md) —
it owns which sources to re-check, how to read them, and how to write the
results back into `skills/select-coder/matrix.md`. This command adds nothing
beyond routing; do not re-derive the procedure here.

- no argument — full refresh of every slice in the matrix.
- `<backend | model>` — refresh only that backend's rows, or that model, and
  leave the rest of the matrix (and its cache date) alone.

Report what changed and what couldn't be re-verified. Only bump `Cached:` on a
full refresh in which every slice was actually checked.
