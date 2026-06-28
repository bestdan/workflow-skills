# gh-issue handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: gh-issue` is configured.

> **GitHub has no issue cap and no true "archive."** Unlike Linear, there is no
> active-issue limit pushing back on the loop, and GitHub issues cannot be
> archived — only **closed**. So this handler is **hygiene-only**: there is no
> cap pressure to relieve. Its retire op is "close stale completed issues (and
> optionally tag them `archived`)" purely to keep `gh issue list` and
> `/list-tasks` views tight. If the user's goal was to escape a cap, tell them
> plainly that gh-issue has none and this step is cosmetic.

## What counts as terminal here

The gh-issue loop marks completed work by **closing** the issue (PR merge with
`Closes #<n>`). So for gh-issue, "terminal" already means **closed**. There is no
deeper archived state to move to. This handler therefore does one of two narrow
things, neither of which changes completion state:

- Add an `archived` label to long-closed issues so they can be filtered out of
  views (`-label:archived`), and/or
- (only if the user explicitly asks) close still-**open** issues that are clearly
  done — but the loop already closes on merge, so this is rarely needed and is
  never done automatically.

## Steps

1. **Resolve config.** Read the `gh-issue:` block from
   `dev_docs/tasks/.task-config.yml` for `repo` (default: current repo via
   `gh repo view --json nameWithOwner --jq .nameWithOwner`) and `labels` (the
   task-loop labels, e.g. `[follow-up]`).

2. **Find candidates — scope to task-loop issues.** List closed issues older than
   the threshold, **filtered to the configured `gh-issue.labels`** so the sweep
   only ever touches loop-filed issues — never unrelated product/bug issues that
   merely happen to be old and closed. This mirrors the label filter the List
   path (`gh-issue.md`) uses. Pass one `--label` per configured label (AND
   filter); `gh` filters closed issues by `closedAt`:

   ```bash
   gh issue list --repo <repo> --state closed --limit 200 \
     --label "<label1>" --label "<label2>" \
     --json number,title,closedAt,labels
   ```

   When `gh-issue.labels` is **empty/unset**, there is no durable task-loop marker
   to scope by — do **not** fall back to sweeping every closed issue. Instead
   **stop** and report that archiving needs at least one configured label to
   distinguish loop issues from the rest of the repo. Keep issues whose `closedAt`
   is more than `N` days before today and that do **not** already carry the
   `archived` label. Never touch open issues.

3. **Always print the candidate list first** (number + title + closed date). If
   `dry-run`, stop here and report "nothing archived (dry-run)".

4. **Retire (label only).** For each candidate, add the `archived` label
   (creating it once if missing). This is the whole retire op — the issue is
   already closed, so nothing about its completion changes:

   ```bash
   gh issue edit <number> --repo <repo> --add-label archived
   ```

5. **Report.** Count labeled, the repo, and a one-line reminder that gh-issue has
   no cap so this is hygiene only. In dry-run, the candidate list plus "nothing
   archived".
