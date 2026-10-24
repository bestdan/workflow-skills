# acli fixtures

`scripts/test_task_dashboard_acli.py` reads every file in this folder, picking
the check by filename prefix. A new file with one of these prefixes is tested
the day it lands:

| Prefix                | What it holds                                                    |
| --------------------- | ---------------------------------------------------------------- |
| `search-*.json`       | stdout of `acli jira workitem search --paginate --json`          |
| `view-*.json`         | stdout of `acli jira workitem view <KEY> --fields … --json`      |
| `list-sprints-*.json` | stdout of `acli jira board list-sprints --state active --json`   |
| `stderr-transient-*`  | stderr of a failure the serial retry absorbs; must not read auth |
| `stderr-auth-*`       | stderr of a logged-out acli; must classify as an auth error      |

**Every file without a `real-` prefix is synthetic.** They were written by hand on a machine
with no `acli`, from the shapes recorded in
`dev_docs/research/2026-10-23-task-dashboard-acli-spike.md`. Keys, names, ids
and dates are invented. The custom field ids are the ones the spike found:
sprint `customfield_10303`, Flagged `customfield_10007`.

Three shapes are guesses the spike did not record:

- the top level of `search` output, taken to be a bare array;
- `list-sprints` output, taken to be an object holding a `sprints` array;
- both stderr files. The auth one in particular is invented.

The parser also accepts an object wrapping the array, under `issues`,
`sprints` or `values`.

`../../dev/fake-acli` answers from these same files, so a sync can run end to
end with no Jira: point `jira.acli_path` at it. Its header lists what it
answers and its `FAKE_ACLI_MODE` failure paths. It reads the synthetic files
only; its globs do not match the `real-` ones.

## Real captures

The `real-` files are redacted, pseudonymised output of acli 1.3.19 against
a work Jira site. The test globs `real-<prefix>` beside each prefix above, so
they get the same checks. In them, `search` prints a bare array and
`list-sprints` an object holding `sprints`, the shapes the synthetic files
assume. `jira_acli.py`'s auth patterns match the logged-out stderr here.

| File                              | What it shows                                                  |
| --------------------------------- | -------------------------------------------------------------- |
| `real-search-assigned.json`       | `search --limit 5 --json`, default fields                      |
| `real-view-links-both-ways.json`  | Blocks links inward and outward, parent, one active sprint     |
| `real-view-sprints-flagged.json`  | three-sprint history ending active, Flagged set, parent        |
| `real-view-mostly-null.json`      | no assignee, sprint, links, labels, priority or due date       |
| `real-view-assigned.json`         | assigned issue, epic parent, two sprints, both closed          |
| `real-list-sprints-active.json`   | `list-sprints --state active` for one board                    |
| `real-stderr-auth-logged-out.txt` | stderr of `search` after `acli jira auth logout`; stdout empty |

Kept: field ids, enum values (status, status category, priority, issue type,
link type, Flagged's `Impediment`), timestamp formats and offsets, and
structure.

Pseudonymised, each with one mapping across every file so links, parents and
sprints still resolve to each other: project keys (`DEMO`, `ALPHA`, `BETA`),
issue numbers, issue and link ids, sprint ids and names (`Sprint <id>`), and
board ids. Every date is shifted by one fixed number of days, with its time
and offset unchanged.

Replaced: summaries, descriptions, display names (`Person A`…), labels
(`label-N`), sprint goals, time zones, and every URL
(`https://jira.example.invalid/redacted`). Dropped: the account id and email
address keys, whose names alone fail the hygiene test, and the avatar URL keys.

`search --fields key` prints one `null` per match, so `search_keys` requests
`status` instead.

The test fails on any file in this folder containing an at-sign, the Atlassian
cloud domain, or an issue key whose project is not in its `PSEUDONYM_PROJECTS`
allowlist, so strip emails, account ids, `self` URLs and the site name, and
pseudonymise keys, before committing one.
