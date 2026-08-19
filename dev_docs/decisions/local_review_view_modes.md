# local-review view modes

Why the review UI has three per-file view modes instead of one split diff, and
why two of the constraints on them look arbitrary from the outside.

## The problem

The split diff is the right default when both sides are live. It is the wrong
default when one side is empty, which is most of what this repo's own review
traffic looks like: plan directories, design docs, and new skills are wholly
new files. Half the screen renders as grey filler, and a markdown plan is read
as source rather than as the document it is.

## Vocabulary

| Term             | Meaning                                                                                        |
| ---------------- | ---------------------------------------------------------------------------------------------- |
| **view mode**    | A per-file enum — `split`, `single`, `preview`. Never a global setting.                        |
| **`single`**     | One number column and one code column, showing the side that has content.                      |
| **`preview`**    | Rendered markdown. The comment target is a block, not a line.                                  |
| **wholly-added** | `status == "added"`. The diff carries every line of the new file.                              |
| **pure-append**  | Zero `-` rows. Gets `single`; never `preview`.                                                 |
| **block**        | A leaf-granularity `marked` token — a list item, a table row, a heading, a paragraph, a fence. |

`parse_diff()` reports the layout fact as `file["single"]`: `"r"` when nothing
was deleted, `"l"` when nothing was added, `None` when both sides are live.
Status is not the discriminator — a pure append is `status: "modified"` and
still one-sided.

## Decisions

**`single` is not a unified diff.** It is defined only when one side is empty.
A file with both `-` and `+` rows is never offered it. Interleaving deletions
and additions into one column is a different feature with a different audience,
and folding it under the same name would have made `single` two concepts
sharing a label. The control therefore renders as `split` alone, `split |
single`, or `split | single | preview`, depending on what the file admits.

**`preview` is offered only for wholly-added files.** This is the constraint
that looks arbitrary and is not. For a modified file the diff carries only hunk
fragments: a fence opened outside the hunk never closes, and a list item
arrives without its parent. Rendering that produces a document that is
confidently wrong, which is worse than no preview at all. Reading the full file
from disk to render it whole was considered and rejected — it works in `--git`
modes and silently does not exist for `--diff-file` or for `gh pr diff` of
someone else's PR, so it would be a mode that appears and disappears with no
explanation the user can act on.

**Overrides persist across `/refresh`; comments do not.** Refresh discards
pending comments deliberately, because the diff moved underneath their anchors.
A view _preference_ has no equivalent staleness, and in `uncommitted` mode you
may refresh repeatedly while an agent edits. Re-picking a mode on every refresh
would be the papercut this change exists to remove. An override that becomes
illegal — the file was previewed as added, then got committed — is dropped back
to the heuristic default rather than honoured.

**Comments are symmetric across modes.** A comment made in `split` is visible
and editable in `single`, and vice versa; switching mode never hides one or
changes where it goes. Anything else lets the "Submit review (N)" counter claim
comments the page cannot show, which is how a comment gets silently abandoned.
This is why `renderFile()` re-materialises saved chips from the `comments`
store rather than treating the DOM as the record.

## Consequences

- The grid is no longer fixed at four columns. `insertAfterRow()` reads
  `grid.dataset.cols`, because a full-width `.cmt-row` inserted mid-row breaks
  the layout, and "mid-row" depends on the column count.
- Collapse and "Viewed" moved out of the DOM into `fileUi`, because a mode
  switch re-renders one file and would otherwise silently uncheck it.
- `preview` brings a vendored `marked` and an HTML sanitizer. Loopback is not a
  trust boundary here — see the threat model in `local-review.md` — so raw HTML
  is off, hrefs are allowlisted, and no image is fetched off-host.

## Deferred

Moving GitHub posting behind the agent. Today `post_pr_comment()` runs inside
the `/submit` handler, so the browser page holds a GitHub write capability.
The intended shape is an intent-only `github: true` flag that the agent acts
on. Orthogonal to view modes, tracked separately.
