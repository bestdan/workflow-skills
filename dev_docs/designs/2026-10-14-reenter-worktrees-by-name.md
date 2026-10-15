---
created: 2026-10-14
status: proposed
tracks: https://github.com/bestdan/workflow-skills/issues/992
---

# Re-enter hook-layout worktrees by name, and refuse the path form

Moved here from bestdan/dotfiles (`dev_docs/designs/`) on 2026-10-14 by [#999](https://github.com/bestdan/workflow-skills/issues/999),
because all the code it proposes lands in this repo. Paths without a repo name
are in this repo; paths in another repo name it.

`EnterWorktree` with `path` always raises an approval prompt for a worktree
outside `.claude/worktrees/`. Under Remote Control nobody can answer it, so
the session stops until someone returns to the terminal; under
`--non-interactive` or `claude -p` the call is denied with no route offered.
For any worktree that sits where
the `workflow-skills` `WorktreeCreate` hook would put it, a prompt-free route
already exists: `ExitWorktree` (`keep`), which does nothing outside a
worktree session, then
`EnterWorktree` with `name`, which the hook answers by handing the existing
worktree back. Agents take the path form anyway, because the plugin's own
guard tells them to. This design makes the plugin compute the name route in
one place, name it in every message that currently says `path`, and refuse a
`path` call that has a name route, so the agent gets a refusal it can act on
instead of a prompt it cannot.

## What is true today

- **The path form prompts, and nothing lifts it.** A model-supplied path
  outside `.claude/worktrees/` prompts whatever the allow rules say, and a
  `PreToolUse` hook answering `allow` does not lift it either. Verified
  2026-09-29 with `claude -p`; recorded in bestdan/dotfiles
  `agents/CLAUDE_ONLY.md`.
- **The name form hands an existing worktree back.**
  `scripts/worktree-create-hook.sh` (workflow-skills 2.79.0) computes
  `<root>/<repo>/<name>` on `<prefix><name>` and, when a worktree of the same
  repo is already there on that branch, prints its path and exits 0. Confirmed
  live on [bestdan/dotfiles#978](https://github.com/bestdan/dotfiles/issues/978): `ExitWorktree` (`keep`), then `EnterWorktree` `name`,
  returned the kept worktree on its branch with no prompt.
- **Ownership is safe on hand-back.** The hook stamps a session as owner only
  when it creates the worktree. A hand-back to a different session rewrites the
  stamp to `shared`, which the remove hook never deletes, so re-entering by
  name cannot let one session's exit remove another's checkout.
- **`name` is refused while the session is already in a worktree**, per the
  `EnterWorktree` tool contract. Moving from worktree A to worktree B by name
  takes two calls: `ExitWorktree` (`keep`), then `EnterWorktree` (`name`).
- **The plugin already computes the name route once, privately.**
  `scripts/reach-pr-branch.sh` returns `enter-name` when the worktree holding a
  ref sits at exactly `<root>/<repo>/<remainder>` on `<prefix><remainder>`, and
  `enter-path` otherwise. co-review is its only caller.
- **The plugin's write guard sends agents to the path form.**
  `scripts/guard-foreign-worktree.py:512` refuses a write into a worktree the
  session has not entered with "Enter it instead: EnterWorktree with path
  {holder}", for every worktree, including ones with a name route ([#987](https://github.com/bestdan/workflow-skills/issues/987)).
- **Guidance has pointed the same way.** bestdan/dotfiles
  `agents/CLAUDE_ONLY.md` said `path` was right for any existing worktree,
  until [bestdan/dotfiles#979](https://github.com/bestdan/dotfiles/pull/979); bestdan/agent-guidance `portable-claude.md:37` said to
  enter with `EnterWorktree` (`path`), until [bestdan/agent-guidance#103](https://github.com/bestdan/agent-guidance/pull/103).
- **An empty user-level `WorktreeCreate` does not unregister the hook.**
  [bestdan/dotfiles#957](https://github.com/bestdan/dotfiles/issues/957) found `"WorktreeCreate": []` in bestdan/dotfiles
  `agents/settings.json` and suspected it overrode the plugin's registration.
  It is closed as not reproducible: with that entry in user settings, the
  plugin's hook still ran (verified with `claude -p` on 2026-10-14, on Claude
  Code 2.1.299 with workflow-skills 2.79.0). Where the hook is not registered
  at all, `name` silently creates a new worktree under `.claude/worktrees/`
  instead of handing one back.

## Proposal

All code changes are in `workflow-skills`; bestdan/dotfiles and
bestdan/agent-guidance change prose only.

1. **A resolver, `scripts/worktree-name-for.sh <path>`** ([#987](https://github.com/bestdan/workflow-skills/issues/987)). Prints the
   `name` whose hook hand-back is that worktree, and exits 0; prints nothing
   and exits 1 when there is none. A path has a name when all of these hold:
   - it is a registered, non-prunable worktree;
   - its common git dir equals the common git dir of the session's cwd, because
     the hook resolves `<repo>` from the cwd and would otherwise create a
     worktree in the wrong repo;
   - its branch is `<prefix><n>`, with `<n>` a valid hook name, prefix and root
     both read through `worktree-config.sh` exactly as the create hook reads
     them;
   - its real path equals the real path of `<root>/<repo>/<n>`.

   `reach-pr-branch.sh` calls it in place of its inline check, so co-review and
   the new hook cannot disagree.
2. **`guard-foreign-worktree.py` names the route** ([#987](https://github.com/bestdan/workflow-skills/issues/987)). When the resolver
   gives a name, the refusal gives the same route every time: "`ExitWorktree`
   (`keep`) first — a no-op when the session is not in a worktree session —
   then `EnterWorktree` with `name: <n>`". Otherwise it keeps naming `path`, and adds
   that the path form prompts and cannot be answered unattended.
3. **A `PreToolUse` hook on `EnterWorktree`, `scripts/guard-enter-by-path.py`**
   ([#989](https://github.com/bestdan/workflow-skills/issues/989)). On a call carrying `path`, it runs the resolver. With a name, it
   denies, and the reason gives the two-step route above. Without one, it
   returns no decision and the harness prompts as today. Any error inside the
   hook also returns no decision.

   **This item waits on a probe** ([#1001](https://github.com/bestdan/workflow-skills/issues/1001)). Only `allow` has been tested against
   this prompt; what a `deny` does has not. Before building item 3, run a
   `claude -p` session against a stub hook that denies `EnterWorktree` `path`,
   and have the agent follow the refusal's route exactly: `ExitWorktree`
   (`keep`), then `EnterWorktree` `name`. It passes only if the agent receives
   the hook's own reason text, `ExitWorktree` returns without error (the
   session starts outside a worktree session, so this runs the documented
   no-op), and `name` returns the existing worktree's path and branch rather
   than a new `.claude/worktrees/<n>`. A bare permission denial does not
   count, because an unanswered prompt also surfaces as a denial under
   `claude -p`.
   If it fails, item 3 is dropped and items 1, 2 and 4 stand. Item 3's tests
   land in the same PR as the hook. Items 1, 2 and 4 do not depend on it.
4. **Prose.** Done: bestdan/dotfiles `agents/CLAUDE_ONLY.md` ([bestdan/dotfiles#979](https://github.com/bestdan/dotfiles/pull/979))
   says to re-enter a hook-layout worktree by name, and to use `path` only
   outside the layout, with someone at the terminal. bestdan/agent-guidance
   `portable-claude.md:37` ([bestdan/agent-guidance#103](https://github.com/bestdan/agent-guidance/pull/103)) now names no tool call
   and states only the general rule, so it cannot go stale when this plugin
   changes. [#992](https://github.com/bestdan/workflow-skills/issues/992) proposes making this repo the one place that describes the routes.

With item 3 in place, every worktree still at the hook's path on the hook's
branch, in the session's own repo, is reachable with no prompt, by any agent,
whatever the guidance it has read. With items 1, 2 and 4 alone, it is
reachable with no prompt by an agent that follows the guard's refusal or the
updated guidance. Two cases still prompt: a worktree at a path the
hook did not choose, and a hook-created worktree switched to another branch
(see **Not decided here**).

## Tests

`reach-pr-branch.sh` and `guard-foreign-worktree.py` already have suites
(`test/reach-pr-branch.bats`, `test/guard-foreign-worktree.bats`), which the
implementing PRs extend. The resolver and `guard-enter-by-path.py` are new
and get new suites.

- **Resolver returns a name:** a hook-layout worktree on `<prefix><n>`; the
  same reached through a symlink (realpath comparison, including macOS
  `/private` fixtures); root and prefix overridden through the `env` entries
  `worktree-config.sh` ranks highest.
- **Resolver returns nothing:** an unregistered path; a prunable worktree; a
  worktree of another repo at a layout-matching path; an unprefixed branch; a
  prefixed branch whose remainder is not a valid hook name; a prefixed branch
  at the wrong path.
- **Hook:** a `path` with a name is denied, the reason quoting `<n>` and
  naming both calls; a `path` with no name, and a `name` call, get no
  decision; a missing or crashing resolver, malformed stdin, or an unreadable
  config get no decision.
- **`guard-foreign-worktree.py`:** a write into a hook-layout worktree is
  refused with a reason naming `ExitWorktree` (`keep`) and `EnterWorktree`
  `name: <n>`; a write into a worktree with no name is refused with the path
  form and the unattended-prompt warning; a resolver failure falls back to
  the path wording.
- **`reach-pr-branch.sh`:** returns `enter-name` and `enter-path` exactly as
  before once it calls the resolver.

## Decisions

### Refuse the path call; do not rewrite it

The hook could instead return `updatedInput` replacing `path` with `name`, so
the call simply succeeds. Rejected for now: whether the harness's permission
check runs on the rewritten input is unverified, and the rewrite cannot help a
session that is already in a worktree, where `name` is refused, so the
refusal path is needed regardless. If the item 3 probe confirms it, a
refusal returns to the agent immediately
with the exact next calls, which replaces both the Remote Control stop and
the unattended denial with a route the agent can take. Revisit with a
`claude -p` probe if the extra `ExitWorktree` turn proves costly.

### Refuse only when a name route exists

Refusing every `path` call would block the worktrees that have no other way
in. The hook cannot tell an attended session from a Remote Control one, so it
cannot refuse "only when nobody can approve". Limiting refusal to paths with a
name route means every call it blocks has a prompt-free way in.

That way in is not free in one case. Entering by `path` never runs
`WorktreeCreate`, so the owner stamp is untouched. Entering by `name` from a
session other than the one that created the worktree rewrites the stamp to
`shared`, so the creator's exit no longer removes it and it needs one manual
`scripts/worktree-remove.sh`. A leftover worktree costs one command later; a
stopped or denied session costs the run. That trade is the decision.

### Give the route unconditionally; never infer the session's state

The refusal names both calls every time rather than "exit if you are in a
worktree". The hook cannot see whether the harness considers the session to
be in a worktree session. The hooks reference
(code.claude.com/docs/en/hooks, checked 2026-10-14) documents no `PreToolUse`
input field for that state; the nearest is `cwd`, which follows the session
into a worktree but does not say how it got there. Inferring it from a linked-worktree cwd or the owner stamp is
cheap, since `guard-foreign-worktree.py` already tests the first, but a cwd
inside a worktree is not the same predicate. The costs are lopsided: an
unneeded `ExitWorktree` is a documented no-op that costs one turn, while a
missing one gets `name` refused, so the agent spends a failed call and must
retry, and an agent that falls back to `path` meets the prompt (or, with
item 3, a refusal). So
neither the hook nor the agent evaluates a condition.

### Fail open

A crash, a missing `python3`, an unreadable config: the hook returns no
decision and the prompt appears as it does today. The alternative, failing
closed, would turn a hook bug into a refusal of every `EnterWorktree` `path`
call, including the ones with no other route.

### One resolver, in the plugin

The mapping depends on `worktree-config.sh` and on the create hook's name
rules, both in `workflow-skills`, so the resolver lives there and ships with
the hook it mirrors. Three callers (co-review, the write guard, the new hook)
each re-implementing the check would drift; `reach-pr-branch.sh`'s copy is
the one that already exists, so it is extracted rather than written fresh.

### Match the target's repo to the session's repo

The create hook derives `<repo>` from the session's cwd. A worktree of a
different repo at a path that happens to match the layout would get a name
that creates a new worktree in the session's repo. The resolver compares
common git dirs and returns no name across repos, leaving that case to the
path form.

## Not decided here

- **Branches without the prefix.** co-review today creates a worktree for an
  unprefixed branch at a path it chooses and enters by `path`. The prompt-free
  alternative is to enter by `name` and `git switch` inside, but the hook then
  refuses to hand that worktree back later, because its branch no longer
  matches. Relaxing the hand-back check (same repo, a worktree the hook
  created, any branch) would close this, at the cost of `name` no longer
  fixing the branch. Separate proposal.
- **The refusal wording.** Left to the PR, except that it names both calls and
  the worktree name verbatim.

## Graduation

- Decision records (workflow-skills): refuse rather than rewrite the path call;
  refuse only with a name route, and fail open; give the route
  unconditionally rather than infer the session's worktree state.
- Conventions: the `workflow-skills` docs for the worktree hooks receive the
  resolver and the new guard; bestdan/dotfiles `agents/CLAUDE_ONLY.md` keeps a
  one-line pointer to the guard in place of the prose rule.
