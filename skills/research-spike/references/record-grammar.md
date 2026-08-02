# research-spike — record grammar

The full field reference for every record kind, the parser's exact rules, and
one worked example of each. `SKILL.md` covers the procedures that write these
blocks; this file is what to check a block against when something doesn't
validate. Every rule below is verified against `scripts/research-spike.py`
directly — the `KNOWN_FIELDS` table, the enum constants near the top of the
file, and the `validate_*` functions are the source of truth, not this
document's memory of the design.

## Parser rules

A record is a fenced code block whose info string is one of `question`,
`obligation`, `decision`, `card` — backtick fences only (3 or more backticks;
there is no tilde-fence support). A fence closes on a line with at least as
many backticks and no info string, so a fence opened with 4 backticks nests a
3-backtick example inside it without closing early — this is how `init`'s
worked example (below) can show a real block without the parser seeing it as
one. A fence whose info string itself contains a backtick (e.g. an inline
code span like `` ```obligation``` ``) is not treated as a fence at all, so it
can't swallow the next real record.

Inside the fence, the grammar is deliberately small:

- **One `key: value` per line.** The split is on the **first** colon only, so
  a value may itself contain colons (a URL, a time).
- **Blank lines are ignored.**
- **No inline comment syntax.** A `#` is part of the value, verbatim — it
  will fail whatever enum check owns that field rather than being stripped.
- **Unknown keys are errors, not ignored.** A `desination:` typo is reported,
  not silently dropped — see the field tables below for what each kind
  accepts.
- **Duplicate keys are errors.** The second occurrence of a key in one block
  is rejected.
- **A line with no colon at all is an error** — `'<text>' is not a 'key:
  value' line`.
- **List-valued fields (`blocks:`, `blocking:`) are comma-separated,** each
  item trimmed of surrounding whitespace. `blocks: a, b` parses to two ids;
  `blocks: ,` parses to zero, which is treated as absent, not present-with-
  nothing.

Content inside an HTML comment (`<!--` … `-->`) or an unclosed/unrecognized
fence is inert: no record, no `### Q<n>.` heading, is read out of it. This is
what lets `init` scaffold a worked example into a fresh `questions.md`
without that example failing its own `validate`. An HTML comment must open at
column 0–3 (CommonMark's rule); at four spaces of indentation it is an
indented code block instead, so a _prose sample showing_ `<!--` does not
accidentally open a real comment region.

### The `none` sentinel

`none` is legal on every record kind, in exactly two shapes — never as a mere
prefix, so `owes: nonetheless the receipt` stays ordinary prose:

- **As the whole value of a field**: `blocks: none: option (A) adds no
  observation and owes no tooling`. The field is `blocks`; its value is the
  sentinel, with `option (A) adds no observation and owes no tooling` as the
  carried reason. A bare `blocks: none` (no reason) is also a legal parse —
  whether a reason is _required_ is a validation rule, not a parser rule (see
  the field tables below).
- **As the block's own leading key**: a line whose key is literally `none`,
  e.g. `none: option (A) adds no observation and owes no tooling`. This is
  the coverage rule's explicit declaration — "this question section owes
  nothing, and here is why" — and, for an `obligation` block specifically, it
  is only accepted **bare**: a block carrying `none:` _and_ other fields
  (e.g. a `destination:` alongside it) is an error, because it is trying to
  be two records — a declaration that nothing is owed, and an obligation
  owing something — at once.

Both shapes parse to the identical internal value (a sentinel carrying the
same reason text), so every rule that reads `none:` has one representation to
handle.

### Ids

Every declared `id:` — and every project/track name — must match
`^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case: lowercase letters, digits, single
hyphens). Ids are declared **bare** in the record and qualified by the script
for reports and uniqueness: `project/track/id` for questions, obligations and
cards; `project/id` for decisions. Qualified ids must be unique across the
**whole tree** and **across record kinds** — a question and an obligation
sharing one string is exactly as ambiguous as two questions sharing it,
because `blocks:`/`blocking:` resolve by that same string and a ledger
counting both would show one name twice.

## Field reference

### `question`

Filed as a `### Q<n>.` section's block in a track's `questions.md`. A section
is everything from a `### Q<n>.` heading to the next heading at level ≤ 3 (a
`####` sub-heading stays inside it); a section with no `question` block, or a
second one, is itself an error.

| Field             | Required                        | Value                                                                                               |
| ----------------- | ------------------------------- | --------------------------------------------------------------------------------------------------- |
| `id`              | yes                             | kebab-case, unique in the tree                                                                      |
| `status`          | yes                             | `open` \| `answered` \| `retired`                                                                   |
| `blocks`          | yes                             | one or more decision ids (comma-separated), or the sentinel `none: <reason>` — a reason is required |
| `answer`          | required iff `status: answered` | one-line conclusion; the evidence itself belongs in the section's prose                             |
| `retired_because` | required iff `status: retired`  | why the question's premise died                                                                     |

**Coverage is enforced per section, unconditionally** — every question
section, whatever its status, must be followed by at least one `obligation`
block (a real one or a bare `none:` declaration) somewhere in the section, or
`validate` fails with "declares nothing it owes." This fires the moment a
section exists, not only once its question is answered — a freshly filed
`open` question with no coverage already fails.

### `obligation`

Filed next to the prose that creates the deferral — inside a question
section, or in a `contracts/` file (see below).

| Field           | Required                                                | Value                                                                                                                            |
| --------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `id`            | yes (unless a bare `none:` block)                       | kebab-case, unique in the tree                                                                                                   |
| `owes`          | yes (unless a bare `none:` block)                       | one line, in the author's own words                                                                                              |
| `destination`   | yes (unless a bare `none:` block)                       | repo-relative path to an **existing regular file** — no absolute path, no `../`, no symlink escape, not a directory              |
| `status`        | yes (unless a bare `none:` block)                       | `open` \| `discharged`                                                                                                           |
| `discharged_by` | required iff `status: discharged`; forbidden iff `open` | free text naming the discharging change (a PR/commit ref) — not path-checked                                                     |
| `blocking`      | optional                                                | one or more decision ids this obligation gates; if written, must name at least one (an empty or all-separator value is an error) |

A **bare `none:` block** (`none:` and nothing else) is the coverage rule's
explicit "this owes nothing" declaration; its reason is required. A block
carrying `none:` alongside any other field is an error.

`destination:` carries two extra rules beyond "must exist": it must resolve
**inside the same project** (a destination landing in another research
project is an error — file the work there instead, and point this obligation
at a receipt card recording the handoff), and a destination inside a
`<name>_plan/` directory is a **warning**, not an error — `/push-plan`
deletes plan directories after migration, so the pointer is legitimate but
should be revisited before that happens.

### `decision`

Stores only the human lifecycle state; everything else (ready/blocked) is
derived on every run, and there is no key to store it with.

| Field              | Required                                                                        | Value                                                                                                                                                                                             |
| ------------------ | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`               | yes                                                                             | kebab-case, unique in the tree (project-scoped)                                                                                                                                                   |
| `state`            | yes                                                                             | `pending` \| `decided`, or `proposed` — **`proposed` only legal inside a track's own `questions.md`**, never in `decisions.md`                                                                    |
| `decided_in`       | required iff `state: decided`; otherwise legal only as retained reopen evidence | pointer to durable evidence — an ADR, a permanent design doc, or a receipt card; must exist, and **must not** be inside a `<name>_plan/` directory (error, not a warning — unlike `destination:`) |
| `reopened_because` | legal only with `state: pending` **and** a retained `decided_in:`               | why a decided decision was reopened                                                                                                                                                               |

A new blocker (`blocks:`/`blocking:`) naming a `decided` decision is an
error unless the decision is explicitly reopened (`state: pending` plus
`reopened_because:` plus the retained `decided_in:` — all three, together;
`reopened_because:` alone does not earn the exemption).

### `card`

One block per file, filed under `tracks/<track>/obligations/`. Every other
file in that directory must be markdown and must carry exactly one card block
(dotfiles like `.gitkeep`/`.DS_Store` are exempt).

| Field             | Required                       | Value                                                       |
| ----------------- | ------------------------------ | ----------------------------------------------------------- |
| `kind`            | yes                            | `stub` \| `receipt`                                         |
| `superseded_when` | required iff `kind: stub`      | the condition of this card's own deletion                   |
| `url`             | required iff `kind: receipt`   | address of the work in the external system it was handed to |
| `handler`         | optional, `kind: receipt` only | which tracker handler created it (e.g. `linear`)            |
| `tracker_id`      | optional, `kind: receipt` only | the tracker's own id for the handed-off work                |

## Worked examples

Each below is a complete, valid record. Checked with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" \
  --root "$(git rev-parse --show-toplevel)" validate
```

**`question`** — inside `tracks/account/questions.md`:

````markdown
### Q3. Must the baseline stop contain a process-group escapee?

```question
id: baseline-stop-escapee
status: answered
blocks: stop-semantics
answer: no — the reaper kills the whole group before the stop returns
```

Measured against a synthetic child that forks a detached grandchild: the
group signal reaches it in every run observed. See `tracks/account/data/`.

```obligation
none: the answer needs no new tooling, only the test already in the suite
```
````

**`obligation`** — a real deferral, next to the prose it belongs to:

```obligation
id: uid-domain-provisioning
owes: the provisioning steps this answer implies
destination: dev_docs/research/demo/tracks/account/obligations/uid-domain.md
status: open
```

**`decision`** — in `decisions.md`, after promotion:

```decision
id: stop-semantics
state: decided
decided_in: dev_docs/research/demo/tracks/account/obligations/stop-semantics-receipt.md
```

**`card`** — a stub, at
`tracks/account/obligations/uid-domain.md`:

```card
kind: stub
superseded_when: the account track files its uid-domain-provisioning task card
```
