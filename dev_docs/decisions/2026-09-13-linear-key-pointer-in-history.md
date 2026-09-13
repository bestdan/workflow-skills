# The Linear key pointer left in public history: rotate, don't rewrite

**Decided 2026-09-13.** Closes the second half of
[#488](https://github.com/bestdan/workflow-skills/issues/488), whose first half —
removing the reference from `HEAD` — landed in
[#489](https://github.com/bestdan/workflow-skills/pull/489) on 2026-09-05.

**Decision: rotate the Linear personal API key and rename the 1Password item.
Do not rewrite history.**

This file deliberately does not quote the reference. Naming the vault and item
here would recreate the disclosure it exists to close out.

## What was exposed

A personal 1Password `op://` reference — a concrete vault/item/field naming a
**Linear personal API key** — was committed to this public repo. Not a
placeholder: it resolved against the maintainer's vault, verified 2026-09-05.

|                     |                                           |
| ------------------- | ----------------------------------------- |
| Introduced          | `8f45273`, 2026-08-01 (#310)              |
| Removed from `HEAD` | `6e56071`, 2026-09-05 (#489)              |
| Public window       | ~5 weeks                                  |
| Sites               | 14 (not the 8 #488 estimated — see below) |

Both commits sit on `main`'s first-parent history, so the reference remains
permanently readable to anyone who clones the repo:

```bash
git log --oneline --first-parent main -S'<the item-name fragment>' -- .
# 6e56071  fix(docs): stop naming the vault ... (#489)
# 8f45273  feat(linear): resolve auth keys through a shared, configurable resolver (#310)
```

**The count was 14, not 8.** #488's estimate came from a joined-literal search,
which cannot match a reference assembled at runtime — and
`scripts/test_secret_resolve.py` assembles one. Five further sites kept the
vault/item pair with the field elided, three of them twice. Recorded here
because the same search would under-count the same way again.

## What it is, stated precisely

- It is a **pointer, not a key**. Resolving it requires an authorized `op`
  session for the maintainer's account, which a reader does not have.
- What it discloses is the **location** of a full-account Linear bearer token —
  the thing a targeted phishing or social-engineering attempt wants, and the
  thing `commands/handlers/linear-config.md:78` already forbids advertising:

  > a personal `op://` pointer is per-clone and still sensitive: committing it
  > advertises which vault/item holds a full-account bearer token

- Until rotation, the pointer in history is an **accurate** map: the local
  `dev_docs/tasks/.task-config.local.yml` still names the same item, so the
  location it discloses is still the live one. That currency — not the string's
  presence — is what made this worth closing out.

## Why rotate rather than rewrite

Rotation attacks the disclosure; a rewrite only attacks the string.

- **Rotation makes history moot.** Once the key is rotated and the item renamed,
  every copy of the reference — in `main`, in every existing clone, in every
  fork, in any mirror or archive — points at nothing that exists. The
  disclosure is not hidden, it is made false. That is a stronger result than
  deletion, because deletion cannot reach copies already made.
- **A rewrite is expensive and still incomplete.** `git filter-repo` plus a
  force push rewrites every SHA since 2026-08-01, breaks existing clones and
  forks, and invalidates the commit hashes cited across `dev_docs/`. GitHub
  additionally retains unreachable objects until support purges them, so the
  blob stays fetchable by SHA after the rewrite. It buys partial removal at
  high cost — and it does nothing about clones already taken during the
  five-week window.
- **Doing both adds nothing over rotation.** Once the pointer is false, removing
  it is cosmetic; the clone/fork breakage is then pure cost.

The general rule this instance follows: **for a leaked pointer or credential,
invalidate the referent — rewriting history is at best a cleanup afterwards,
never the remedy.**

## Rejected alternatives

- **Rewrite history (with or without rotation)** — rejected above: high cost,
  incomplete removal, and unnecessary once the referent is invalid.
- **Accept the residual risk and document it** — rejected. It is the correct
  reading of the _severity_ (a location, not a credential) but not of the
  _cost_: rotating a personal API key is a few minutes' work, which is far less
  than the value of making a five-week public disclosure inert. Accepting risk
  is for when mitigation is expensive, and here it is not.

## Follow-through

The rotation is a maintainer action against 1Password and Linear, not a repo
change, so it cannot be verified from this checkout. Steps:

1. Revoke the existing Linear personal API key and issue a new one.
2. Rename or move the 1Password item so the disclosed vault/item/field no longer
   resolves.
3. Update `linear.api_key_ref` in the **gitignored**
   `dev_docs/tasks/.task-config.local.yml` to the new location — never the
   committed `.task-config.yml` (`commands/handlers/linear-config.md:78`).
4. Confirm a Linear read fast path still resolves its key, e.g. `/doctor`.

Step 2 is what makes the history inert; steps 1 and 3 are what keep the loop
working. A rotation without the rename leaves the disclosed location still
correct and only changes the secret behind it — weaker, though still a large
improvement.

## What prevents a recurrence

Already in place, and unchanged by this decision:

- `commands/handlers/linear-config.md:78` states the rule and names
  `.task-config.local.yml` as the only home for a secret-bearing field.
- `dev_docs/auth_key_access.md:173` requires references to be reduced, never
  printed whole; `commands/handlers/assets/_secret_resolve.py:53` implements
  that as `op://<vault>/…`.
- Docs and tests use two non-resolving forms — `op://Private/Linear API/credential`
  in prose and `op://TestVault/Item With Spaces/…` in tests. Both keep the space
  in the item title, which is the property the passages exist to demonstrate.

What was missing was any check that the committed tree honors the rule. Nothing
in `just check` greps for a resolvable personal reference, which is why this
survived five weeks. Filed as
[#598](https://github.com/bestdan/workflow-skills/issues/598) rather than bundled
here, since it is a gate change and this is a decision record.
