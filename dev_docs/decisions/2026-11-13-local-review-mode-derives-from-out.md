---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# `local-review`'s server mode derives from `--out`, with no mode flag

## Context

Threaded review needs the server to stay alive across rounds and expose reply
endpoints, but only when an agent is reading the rounds. Every agent launch
passes `--out`; the hand-run recipe never does.

## Decision

There is no mode flag. An explicit `--out` without `--once` selects threads
mode, `--out` with `--once` is one-shot, and no `--out` is human-only, which
still writes `pr_comments.json`. `--out`'s argparse default moved to `None` so
that "given" means "given explicitly". `--once` means "exit after one
successful submit" in every mode. Landed in [#418](https://github.com/bestdan/workflow-skills/pull/418).

## Consequences

- Good, because the reply affordance appears exactly when a reader for the
  rounds exists: thread UI with no listener, or an agent launch with no reply
  channel, cannot be configured.
- Bad, because the meaning of a launch changed: every existing
  `--out`-without-`--once` launch, including four harness cases, became a
  threads-mode server with a larger payload.

## Revisit when

- A consumer needs a stay-alive server that writes the one-shot payload shape.

## Confirmation

`scripts/test-local-review.sh` asserts the payload shape per launch shape and
that the thread endpoints 404 outside threads mode.

## Alternatives

- **A `--threads` flag:** an explicit knob that can contradict the channel it
  describes, which adds two ways to be wrong that derivation removes.
