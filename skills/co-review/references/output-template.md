# co-review output template

The shape step 9 fills. Rules live in [`../SKILL.md`](../SKILL.md) at step 9,
not here.

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

## Section 3 under `--post` (and `--post --non-interactive`)

```markdown
## Calls for you to make

Post candidates (high + medium) — vet before anything is posted:

1. `<file>:<line>` — <the issue> → <the suggested fix> [<tier>]
2. …
```

## Section 3 under `--non-interactive`, default disposition

```markdown
## Calls for you to make

Deferred judgment calls (medium, none applied):

- `<file>:<line>` — <the issue> → <the recommended fix>

Deferred verification items (none run):

- <command or action> — <environment> — <what a pass looks like> — <who runs it>
```

## Section 2 under `--post`

Same, minus the **Auto-fixing** field.
