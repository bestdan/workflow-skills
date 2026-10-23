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

**Every file here today is synthetic.** They were written by hand on a machine
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
answers and its `FAKE_ACLI_MODE` failure paths.

#1087 adds redacted real captures beside these. The test fails on any file in
this folder containing an at-sign or the Atlassian cloud domain, so strip
emails, account ids, `self` URLs and the site name before committing one.
