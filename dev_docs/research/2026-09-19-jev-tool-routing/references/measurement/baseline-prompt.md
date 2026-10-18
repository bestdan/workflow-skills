# The baseline prompt, as given

Verbatim, apart from the 36 jobs, which were pasted in as
`jev-pick-tool.py --cases` printed them (`<id>: <task>`, one per line) and are
omitted here because that command regenerates them exactly.

Three Claude subagents received this, each in a fresh context with no access to
this conversation, and each wrote its answers to its own file. Those answers are
`baseline/agent-{1,2,3}.json`, graded by `jev-pick-tool.py --score`, which is the
same scorer the Jev side goes through.

The read-only constraint is the whole measurement: an agent that grepped the
repository would find the labels in `jev-pick-tool.py` and score 36/36 by
reading the answer key.

---

You are answering a routing quiz. Answer from your own judgment only.

HARD CONSTRAINT: Do not read, search, or explore any files in any repository. Do
not look for the answers anywhere. The only tool you should use is a single Write
at the end. If you find yourself wanting to grep or read a file, stop — that
invalidates the measurement.

## The decision you are making

For each job below, decide which of three tools should do it:

- **code** — computable exactly from what you have
- **jev** — one of a fixed set, a yes/no, or a point on a scale, judged from text
  you already have. (Jev is a "typed judgment" model: you give it a state plus
  yes/no, multiple-choice, or rating questions, and it returns typed answers with
  probabilities. It generates no text, cannot fetch anything, and cannot do
  multi-step reasoning.)
- **llm** — text a human reads, or it needs steps or fetching

Exactly one answer per job. No hedging, no "both".

## The jobs

[the 36 lines `jev-pick-tool.py --cases` prints]

## Output

Write a single JSON object to <path> mapping every job id to exactly one of
"code", "jev", "llm". All 36 ids must be present. Nothing else in the file.

Then reply with just the word done.
