# The incumbent panel: what the three runs measured (2026-09-23)

The evidence #814 adds under this record. It reports what the three runs measured
and what the shared scorer says about them. It does not write the verdict. That is
#815's job, and #815 carries the limits the record lists.

`agent-{1,2,3}.json` are the three runs as
[`../../run-baseline.py`](../../run-baseline.py) wrote them. Each reply is kept
verbatim under `raw`. There were 150 calls: all 50 cards were answered by every
agent, no call failed, and no call needed a retry.

## Setup: what "the incumbent" was in this run

- **Model:** `claude-opus-5-5`, pinned on every call, and the only model any call
  was served by (each card's `served_models`). The subagent inherits its caller's
  model. Opus 5.5 is the model the session that ran this delivery runs on, so the
  pinned id stands for that production setup.
- **One fresh `claude -p` process per card** (Claude Code 2.1.281), in an empty
  temporary directory. User and project settings, plugins, hooks and MCP servers
  are off, and no tools are offered: `--setting-sources "" --strict-mcp-config
  --tools ""`. The no-file-read constraint is therefore enforced mechanically, not
  only by the prompt. The effort level is the CLI default. `invocation` in each
  file is the exact argv.
- **The prompt** is `build-corpus.py --prompt <id>`, byte for byte, sent on stdin.
- **Order:** agent 1's 50 cards, then agent 2's, then agent 3's. The runs were
  sequential, one call at a time, and the last finished at 22:42 EDT.

This is a stand-in for an `Agent`-tool spawn, not one. It has a CLI process start
that a spawn may not pay, and it lacks the parent session's system prompt, which
a spawn may carry. #815 should treat the latency as the cost of this shape.

## Latency, tokens and dollars per packet (the subagent side)

Wall clock is measured around the whole process, from start to exit, so it
includes the CLI's startup and context load: "profile needed" to "profile in
hand".

| per packet (n = 150)                     | median  | mean    | p95   | min   | max   |
| ---------------------------------------- | ------- | ------- | ----- | ----- | ----- |
| wall clock, s                            | 3.796   | 3.920   | 5.013 | 3.285 | 7.600 |
| of which API time (`duration_api_ms`), s | 2.736   | —       | —     | —     | —     |
| uncached input tokens                    | 2       | 2.0     |       |       |       |
| cache-read tokens                        | 531     | 531.0   |       |       |       |
| cache-write tokens (1h TTL)              | 5,282   | 5,403.5 |       |       |       |
| output tokens                            | 201     | 209.0   |       |       |       |
| USD at list price                        | 0.04664 | 0.04752 |       |       |       |

The CLI's own overhead, meaning wall clock minus API time, has a median of 1.04 s.

**Dollars** are priced at Opus 5.5 list rates: $4 uncached, $0.20 cache read,
$8 cache write and $20 output, per million tokens. The cache-write rate is the
1-hour TTL rate, because every cache write these calls made was a 1-hour entry
(810,521 tokens at 1h, none at 5m). Recomputed from the committed token counts,
the 150 calls total **$7.1283**. That matches the CLI's own `total_cost_usd`
summed over the same calls, to within float rounding.

**Cache.** The skill prompt was cache-warm on **0 of 150** calls. Each call reads
only the CLI's own 531-token system prompt from cache. It writes the ~5.3k-token
skill prompt afresh, because the prompt is one message with the card at its end,
so no earlier call leaves a cache entry that stops before the card. `cache_warm`
in these files means that most of a call's input was read from cache.
`run-baseline.py`'s `is_cache_warm` explains why "any cache read" would have
marked all 150 calls warm. The incumbent's dollars here are therefore the cold
figure. An estimate, not a measurement: if the skill text were read from cache and
only the ~400-token card written, a call would cost about $0.009 (5.5k cache-read,
0.4k cache-write and 0.2k output tokens), roughly 5× less than the $0.0475 measured.

## Cross-agent agreement, per dimension

`A_panel` is the scorer's mean pairwise agreement among the three agents. The
pair and unanimity counts are plain counts over the same parsed answers. No answer
was missing or outside its enum, and no card was contested (every card had at
least a 2-of-3 majority on every dimension).

| dimension                  | A_panel | pairs 1–2 / 1–3 / 2–3 (of 50) | unanimous | values given (150 answers)           |
| -------------------------- | ------- | ----------------------------- | --------- | ------------------------------------ |
| `complexity`               | 0.973   | 49 / 48 / 49                  | 48 / 50   | mechanical 18, standard 109, hard 23 |
| `creativity`               | 0.973   | 48 / 49 / 49                  | 48 / 50   | low 113, medium 37                   |
| `autonomy`                 | 1.000   | 50 / 50 / 50                  | 50 / 50   | bounded 150                          |
| `speed_sensitivity`        | 1.000   | 50 / 50 / 50                  | 50 / 50   | low 150                              |
| `cost_sensitivity`         | 0.987   | 49 / 50 / 49                  | 49 / 50   | low 143, high 7                      |
| `verification_criticality` | 0.920   | 45 / 45 / 48                  | 44 / 50   | low 108, high 42                     |

The three agents agreed on all six dimensions on **40 of 50** cards. The panel's
agreement on the whole tuple of measurable dimensions is 0.880.

## Scored through the shared `--score` path

The Jev side is `../jev-run.json` (#850). Both sides went through the same
agreement function, so the command below reproduces this output from the committed
files:

```
D=dev_docs/research/2026-09-22-assess-task-comparison/references
python3 $D/compare-assess-task.py --score $D/measurement/jev-run.json \
    $D/measurement/baseline/agent-1.json $D/measurement/baseline/agent-2.json \
    $D/measurement/baseline/agent-3.json
```

```
50 cards, 3 Jev passes (jev-1.13.0), 3 agents (claude-opus-5-5); skill prompt cache-warm on 0 of 150 incumbent calls

latency   incumbent median 3.796s p95 5.013s | Jev median 0.397s p95 0.465s
R = 5.013 / 0.465 = 10.79  ->  δ 5 points, δ_tuple 10 points
dollars   incumbent $0.047522 | Jev $0.000061 per task  ->  floor PASS

  dimension                   meas  A_panel   A_jev   S_jev  A_const  cont  miss J/P   a b c d  (d: pooled/cards L:H)  match
  complexity                yes/13    0.973   0.800   0.973    0.727     0       0/0   X . . X  (32/12 0:32)  NO
  creativity                yes/13    0.973   0.800   0.987    0.753     0       0/0   X . . X  (28/10 3:25)  NO
  autonomy                    no/0    1.000   0.813   0.960    1.000     0       0/0   X . X X  (28/11 0:28)  n/a
  speed_sensitivity           no/0    1.000   1.000   1.000    1.000     0       0/0   . . X .  (0/0 0:0)  n/a
  cost_sensitivity            no/2    0.987   0.793   1.000    0.953     0       0/0   X . X X  (30/10 3:27)  n/a
  verification_criticality  yes/14    0.920   0.773   1.000    0.720     0       0/0   X . . X  (30/10 24:6)  NO

whole profile (exact agreement on the tuple of measurable dimensions):
  all (complexity, creativity, verification_criticality): A_panel 0.880, A_jev 0.491, S_jev 0.960, A_const 0.420 -> FAIL
  matching: empty tuple

unmeasurable (no accuracy claim; #815 decides unverified vs constant): autonomy, speed_sensitivity, cost_sensitivity

verdict: DON'T ADOPT — no measurable dimension matches
```

Some of this bears on #815 but is not decided here:

- **`R` = 10.79 lands in the top band, but only just.** An incumbent p95 below
  4.65 s (10 × Jev's 0.465 s) would drop it to the 0-point band. The CLI's ~1 s startup is
  part of the 5.01 s, which is why the stand-in's shape (Setup, above) matters.
- **Stability is the incumbent's.** The panel is at least as steady as Jev's own
  passes on every measurable dimension except `verification_criticality`
  (A_panel 0.920 against S_jev 1.000). The routing record found the same thing.
- **Jev's misses have a direction.** It errs high on `complexity` in 32 of 32
  pooled misses, on `creativity` in 25 of 28, and on `autonomy` in 28 of 28. It
  errs low on `verification_criticality` in 24 of 30. That is gate (d)'s failure
  mode, not scattered noise.
- **The expected unmeasurables.** The record expected `speed_sensitivity` and
  `cost_sensitivity` to be unmeasurable, and both are. `autonomy` is too: the
  panel said `bounded` on all 150 answers.
