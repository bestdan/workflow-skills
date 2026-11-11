# Merge autonomy: how an agent lands a PR

An agent merges a PR only when `scripts/land-pr.sh` lands it, and the lander
merges only when every gate below passes on the exact head it judged. The
repo's `AGENT_LAND` variable must be `on`, `merge-tier.py` must tier the PR
`auto` under the policy on its base branch, the co-review evidence for that
head must be clean, the `co-review` status must be `success`, and every other
check must be green. Any gate that fails, or cannot be read, is a hold. A hold
leaves the PR for the owner and is reported with its reason, never retried.

Landing is in shadow in every repo that has a merge policy: `AGENT_LAND` is
unset in all four (workflow-skills, finplan, agent-guidance, dotfiles), and no
allow rule for `land-pr.sh` exists, so the lander only posts its "would land"
comments. GitHub owns the variable, so check it rather than trusting this
paragraph: `gh variable list -R <owner/name>`.

The runtime procedure, when `/deliver-task` runs the lander and what it does
with each output line, is
[`skills/deliver-task/references/landing.md`](../skills/deliver-task/references/landing.md).
Every check, and the exact output format, is in the docstring of
[`scripts/land-pr.py`](../scripts/land-pr.py). The undo is
[`/revert-landing`](../commands/revert-landing.md). What is not built yet (the
Phase 2 gates, the remaining per-repo rollout) is in the
[design](designs/2026-10-03-graduated-merge-autonomy.md).

## How the gates fit together

Each gate can only hold a PR; none can promote one another gate held. In the
order `land-pr.py` reads them:

1. **PR shape.** The PR is open, not a draft, and based on the default branch.
2. **Tier.** [`scripts/merge-tier.py`](../scripts/merge-tier.py), run on the
   base branch's [`.github/merge-policy.yml`](../.github/merge-policy.yml),
   returns `auto` only when no rule fires. Each of these makes it `human`: a
   changed path that is a landing-gate file, matches `always_human`, or
   matches no `auto` glob; more changed lines than `size_cap`; a title that is
   not a Conventional Commit or whose type is outside `allowed_types`; `!`
   after the type or a `BREAKING CHANGE` footer; a `repo_rules` lint; the
   `merge:hold` label; `AGENT_LAND` not `on`; a policy the parser cannot read.
3. **Evidence.** The co-review record for this exact head, read through
   [`scripts/co-review-evidence.py`](../scripts/co-review-evidence.py), exists
   and shows no unresolved finding, no open verification item, no
   consult-decided call below `consult_bar`, no preference call, no
   stack-reaching call, and no recommendation of another round.
4. **`co-review` status.** `success` on the head. `/co-review` posts it
   ([`status.md`](../skills/co-review/references/status.md)) and the
   `protect-main` ruleset requires it, so it binds human merges too.
5. **Checks.** At least one check has reported, every check on the head is
   green, and every check the base branch's rulesets require has reported.

Three properties hold across the gates:

- **The tier is rules, never a model.** `merge-tier.py` reads no review
  evidence and no model output. Co-review, the reconciler and `/consult` reach
  the decision only through the evidence record, which can only add holds.
  Why: [rules tier, models only demote](decisions/2026-10-03-rules-tier-models-only-demote.md).
- **Every verdict binds to one head.** The evidence record, the `co-review`
  status and the checks are all read for `headRefOid`, and the merge passes
  `--match-head-commit` with that SHA, so a push after the judgment makes
  GitHub refuse the merge. A round that applied fixes leaves its fix commit
  unreviewed, so the PR needs another round. The exception is a passing round
  that applied no blocking fix and pushed exactly one fix commit: `scripts/co-review-carry.py` carries its
  `success` to that commit, marked as carried, and the lander says so.
  Why the status is a required check rather than a script rule:
  [review-complete is a required status](decisions/2026-10-03-review-complete-is-a-required-status.md).
- **The PR cannot grade itself.** The lander loads `merge-tier.py` and the
  evidence reader from its own installed directory, never the PR's tree, and
  reads the policy from the base branch, never the head. The landing-gate
  files (`SELF_PATHS` in `merge-tier.py`: the policy, `merge-tier.py`,
  `land-pr.py`, `land-pr.sh`, `co-review-evidence.py`, `co-review-stamp.py`)
  are human-tier whatever the policy says.

A landing squash-merges with a body listing the reasons and evidence, ending
in two trailers, `Landed-by: land-pr.sh` and `Co-review-head: <sha>`. The
`landed-by-agent` label and a PR comment repeating the body follow as best
effort. Why the lander is a script the session runs, rather than an Actions
workflow, and why the audit record rides in the commit:
[the lander runs in the session](decisions/2026-10-03-lander-runs-in-the-session.md).

`/promote-tasks` runs the path rules alone on a task before any PR exists
([`scripts/tier-forecast.py`](../scripts/tier-forecast.py), the policy read
from the default branch), and a task forecast `human` is promoted only once its
Intent block is confirmed. The forecast never decides a merge: the lander
re-tiers the real PR.

## Switching landing on and off

- **Per repo: `AGENT_LAND`.** A repository Actions variable. The lander reads
  it before anything else and goes no further unless it is `on`; unset, `off`,
  or any other value is off. The owner switches it:
  `gh variable set AGENT_LAND --body on -R <owner/name>`, and `--body off` (or
  `gh variable delete AGENT_LAND -R <owner/name>`) to kill landing at once.
  While it is off, the shadow run still posts its "would land" comment and the `TIER:` line is
  still computed as if it were on, so the shadow record keeps accumulating.
- **Per PR: `merge:hold`.** Any label of that name makes the tier `human` with
  the reason `label merge:hold`. The lander runs only at `/deliver-task`'s
  hand-off, so removing the label later does not land the PR by itself:
  re-run the lander, or merge by hand.
- **Per run: `--no-land`.** `/deliver-task --no-land` skips the real run.
  `/auto-pilot` passes it for a stack parent, so a repo that deletes merged
  branches does not delete the branch a child is based on.
- **Stacks land by hand.** A PR based on anything but the default branch
  always holds, because the policy on that base is one an agent can edit. A
  stack lands bottom-up, by the owner.

**Merge authority.** This plugin ships no permission rule. In the harness
settings that grant it, the one allow rule names the installed plugin's
`land-pr.sh` by its absolute path, globbed over the version directory; never a
repo-relative or working-tree copy, which the authoring agent can edit first.
Raw merge commands stay owner-approved, and the guard hook that refuses other
merge routes is a precondition of that allow rule: remove the hook and the
rule goes with it. Without the rule, an unattended run gets a permission
denial, which `landing.md` treats as a hold. Why:
[the lander runs in the session](decisions/2026-10-03-lander-runs-in-the-session.md).

## Audits and reverts

[`scripts/merge-audit.py`](../scripts/merge-audit.py) is read-only and has
three modes. Its docstring has the usage and output lines. Nothing schedules
it; run it weekly from a session with `gh` auth (the docstring has a `/loop`
line that does).

- **`review`**: merged PRs whose final head carries no `co-review` `success`,
  each named `bypass`, `ungated` or `unknown`. The list should hold only
  deliberate bypasses. The `protect-main` ruleset keeps its admin
  bypass, for the release workflow's token, so an admin-token merge skips the
  required check and shows up here instead
  ([`releasing.md`](releasing.md); why the bypass stays:
  [review-complete is a required status](decisions/2026-10-03-review-complete-is-a-required-status.md)).
- **`shadow`**: the would-land count against the 30 a repo needs before
  landing is switched on there, with commits pushed after the judged head and
  fix or revert PRs that cite a would-land PR within 7 days. `--policy <file>`
  re-tiers the shadowed PRs under a candidate policy, which is how to test a
  tuning before proposing it.
- **`landed`**: every merge carrying the `Landed-by` trailer, with its
  follow-ups, and any that lacks the `landed-by-agent` label. Use a 14-day
  `--since` so each landing has had its full 7-day follow-up window.

The audit lists; the owner judges. Whether a later commit was a substantive
owner change, or whether a follow-up's defect was one a gate could have
caught, is the owner's call, and a landing whose defect no gate could catch is
a reason to narrow the `auto` globs.

To undo a landing, run [`/revert-landing <pr>`](../commands/revert-landing.md).
It opens a revert PR titled so it releases and so the `landed` audit finds it,
and never merges it.

[`scripts/review-queue.py`](../scripts/review-queue.py) is the other half:
the open PRs that wait on the owner, each with the decision brief
`/deliver-task` writes
([`decision-brief.md`](../skills/deliver-task/references/decision-brief.md)).
`--post` keeps it in one pinned issue. With `--limit` it is the gate
`/do-tasks` and `/auto-pilot` check before claiming, set by
`review_queue_limit` ([`commands/task-config.md`](../commands/task-config.md)).

## Tuning the policy

Carve-outs come from the reasons each shadow "would land: no" comment lists.
In order of cost:

1. **Edit `.github/merge-policy.yml`:** the `auto` and `always_human` globs,
   `size_cap`, `allowed_types`, `consult_bar`. `always_human` is checked
   before `auto`, and a glob cannot exclude a sub-path, so to carve a sub-path
   out of a broad entry such as `commands/**`, replace the entry with narrower
   globs. `**` matches across directories, `*` and `?` within one segment. A
   renamed or copied file is checked under both its paths. The file must stay
   inside the YAML subset `load_policy` parses (flat `key: value`,
   `key: [a, b]`, and `key:` followed by `- item` lines).
2. **Add a repo rule** for a judgment the paths cannot express, such as "a
   script changed without its test pair": a function in `merge-tier.py`'s
   `REPO_RULES`, switched on by name under `repo_rules`. The one rule today is
   `prompt-paths-need-releasing-type`.
3. **Add a policy key** for a new kind of condition, such as a `generated:`
   list left out of the size count. Add it to `POLICY_KEYS` (and `LIST_KEYS`
   for a list) and validate it in `_validate`. An unknown key fails the parse,
   so a misspelt key cannot widen a tier by accident.

`consult_bar` (0-100, default 85) is read by the lander's evidence check, not
by `merge-tier.py`, which only validates it; `/promote-tasks` uses the same
value as the bar its Intent confirmer must reach.

The starting values and why:
[starting thresholds](decisions/2026-10-03-merge-autonomy-starting-thresholds.md).
Why releasing title types are in the slice:
[release-cutting titles](decisions/2026-10-31-release-cutting-titles-in-the-first-slice.md).
Why `dev_docs/**` is human:
[no dev_docs in the first slice](decisions/2026-10-03-no-dev-docs-in-the-first-auto-slice.md).

However the policy is tuned, the lander reads it from the PR's base branch,
the gate files stay human, and anything the parser cannot read makes the PR
human. So a change to the policy or to `merge-tier.py` lands only by the
owner's hand, and applies to PRs judged after it lands.

## Accepted risks

These hold today and are accepted. Each names what would reopen it.

- **Silent-read bias.** The history the slice was sized on may undercount the
  owner's role. The shadow record is the correction: if would-land PRs draw
  substantive owner changes, narrow the slice.
- **Gaming by the author.** An agent could split a change to stay under the
  size cap, or pick a title to fit a tier. The tier reads paths, so a split
  still hits the same `always_human` globs, and the title rules cover the
  release lever.
- **A self-attested status and trailer.** The `co-review` status proves only
  that some writer posted a status with that name, and every agent holds the
  same token, so an authoring agent could post it without a completed review.
  The evidence record and the PR stamp are posted with the same token, so
  they add no independence. The `review` audit catches a missing status, not a
  forged one. The `Landed-by` trailer is self-attested the same way: the
  trailer-keyed audits list what claims to be a lander merge, not what is
  one.
- **A merge the guard cannot read.** The guard hook reads only the Bash
  command text, so a merge inside a script or interpreter the agent wrote is
  invisible to it. Such a merge still needs the required checks, and the
  `review` audit catches one without a `co-review` `success`; one that skipped
  only `merge-tier` leaves no trailer and nothing flags it. The installed
  `land-pr.sh` sits outside the sandboxed write set, which makes it
  tamper-resistant, not tamper-proof: an unsandboxed command or an Edit/Write
  call can change it, or add a version directory the allow rule's glob
  matches. A hash check inside the lander would not help, because the same
  edit can remove it.
- **Consult correlation.** `/consult` is Claude-only, so its agreement
  correlates with the author's. If consult-decided technical calls are later
  overturned, raise `consult_bar` or stop letting consult-decided calls keep a
  PR landable.
- **Install base.** Releasing title types land, so a bad landing ships a
  release that reaches every user before the revert does. `/revert-landing` is
  the undo. If many users besides the owner pull releases, take releasing
  types out of `allowed_types`.
