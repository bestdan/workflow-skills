# co-review output template

The skeleton step 9 fills when it presents a review. The rules that govern it —
the section order, the verdict roll-up, the one-at-a-time rule, and the two
disposition reshapes — are in [`../SKILL.md`](../SKILL.md) at step 9 and bind
whether or not this file is read. This file is the shape; that file is the law.

Fill it top to bottom. Don't reorder the sections, and don't drop one that came
out empty — say it is empty.

```markdown
## Overview

**Verdict:** <pass | pass with suggestions | blocking> — <one line of why>
**Reviewers:** <which ran, which timed out, which were skipped and why>

## Findings & verification

**Auto-fixing** (high confidence) — <what you will change at step 10, or "none">
**Skipped** (low confidence) — <each one named, with its reason, or "none">
**Verification tests** — <the real-machine checks, or "none needed — <why>">

## Calls for you to make

<the first open item, as one yes/no question — or "none">
```

## What changes per disposition

Only the contents change. The three sections, and their order, never do.

**`--post` (someone else's PR).** Section 2 drops the **Auto-fixing** field —
`--post` never edits the tree, so it is absent rather than empty — and section 3
becomes the post-candidate list, because vetting it (step 10) is the call the
user makes:

```markdown
## Calls for you to make

Post candidates (high + medium) — vet these before anything is posted:

1. `<file>:<line>` — <the issue> → <the suggested fix> [<tier>]
2. …
```

**`--non-interactive` (default disposition).** Nobody is there to answer, so
section 3 becomes the deferred log the `/deliver-task` caller reads:

```markdown
## Calls for you to make

Deferred judgment calls (medium, none applied):

- `<file>:<line>` — <the issue> → <the recommended fix>

Deferred verification items (none run):

- <command or action> — <environment> — <what a pass looks like> — <who runs it>
```

**`--post --non-interactive`.** Section 3 stays the post-candidate list above —
step 10 skips the vetting gate and step 11 posts that set. The verification list
stays in section 2, user-facing only; it is never posted.
