# Decision brief in the PR body

Step 4 of `/deliver-task` writes a three-line decision brief into the PR body
of every PR in a repo with a merge policy (see "When").
`scripts/morning-digest.py` collects the briefs of the open human-tier PRs into
one digest, so the owner can decide a whole stack in one pass instead of
opening each PR. The script parses the section, so its shape is fixed.

## The section

```markdown
## Decision brief

- **Changed:** <what changed, by behaviour, one line>
- **Judge:** <the one thing to judge>
- **A no invalidates:** <what a "no" would invalidate>
```

Keep the heading exactly `## Decision brief` and the three bold labels exactly
as shown. The section runs to the next `#` or `##` heading. A long line may wrap
onto an indented continuation line.

## What each line says

- **Changed:** what a user or caller now sees happen differently. Behaviour,
  not files: "`/do-tasks` stops claiming when 5 PRs wait", not "edited
  `do-tasks.md`".
- **Judge:** the single judgment most worth the owner's time, the one the
  tests cannot settle. When several exist, name the one a wrong answer costs
  most on, and leave the rest to the how-to-evaluate steps.
- **A no invalidates:** the concrete consequence of rejecting it: which PRs
  above it in a stack, which follow-up task, or which design decision would
  have to change. "Nothing else" is a valid answer.

## When

Write it with the PR body in step 4, for every PR in a repo that has
`.github/merge-policy.yml` on the base branch the stack lands on (a lone PR's
own base). That file marks a repo whose
owner reads the digest. Where it is absent, as in a repo another team reviews,
skip the section: its template governs the body, and the how-to-evaluate steps
still carry the same judgment. An `auto`-tier PR's brief is harmless, and the
tier can change when a round adds a file. At hand-off,
refresh it if the co-review rounds changed what the PR does. A PR without a
complete brief still appears in the digest, marked `brief missing`.
