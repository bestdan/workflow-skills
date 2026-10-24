# claude fixtures

stdout of `claude -p --input-format stream-json --output-format stream-json --verbose --no-session-persistence`
answering an `initialize` and a `list_cloud_sessions` control request, the
read `claude_remote.py` makes.

| File                            | What it holds                                         |
| ------------------------------- | ----------------------------------------------------- |
| `list-ok.jsonl`                 | a `status: ok` page of six sessions, `truncated` true |
| `list-started-by-session.jsonl` | the refusal a process inside Claude Code gets         |
| `list-not-signed-in.jsonl`      | the refusal when the CLI has no sign-in               |

**All three are synthetic.** No real page was captured: the read is refused
inside a Claude Code session, which is where they were written. The shapes
come from two sources. The research record
`dev_docs/research/2026-10-23-claude-remote-session-read.md` gives the key set
the owner's real read returned. The CLI's own schema (Claude Code 2.1.310)
gives the envelope, a `control_response` holding
`{subtype: "success", request_id, response: <payload>}`, and the refusal
payload `{status: "unavailable", reason, message}`. The `system` and `result`
lines and the `initialize` reply are trimmed placeholders.

Ids, titles, repositories, branches and dates are invented, and titles are
`Session <n>`. The page covers the cases the adapter maps: the four
statuses seen on the wire (`requires_action`, `running`, `idle`, `archived`),
one with no `status` (the CLI reads that as idle), an empty title,
sessions with and without `repository` and `branch`, and both environment
kinds: four `anthropic_cloud` sessions, which the adapter keeps, and two
`bridge` ones, which it drops.

`../../dev/fake-claude` answers from these files.
