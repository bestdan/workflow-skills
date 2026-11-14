---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# An unavailable credential is a typed, redacted category, and the caller decides what it means

## Context

Before [PR #310](https://github.com/bestdan/workflow-skills/pull/310) the assets
interpolated the full reference into stderr, and only markdown told agents not to repeat
it, so the reference leaked through logs and direct invocation. A bare exit status could
not tell a lapsed session (where `linear-common.md` requires telling the user to run
`op signin`) from a missing item or binary.

The five consumers also disagree about what unavailability means. `linear-ready.py`,
`linear-scan.py` and `linear-relations.py` have an MCP floor. `linear-archive.py` and
`linear-false-closures.py` have none.

## Decision

The helper raises `SecretUnavailable(category, message)`, where the message is already
redacted (`op://<vault>/…` for `op://` references, "configured secret reference"
otherwise) and a resolver's own stderr is never passed through. The categories are
`unconfigured`, `no-session`, `not-found`, `denied`, `no-binary`, `timeout`, `empty`,
`malformed-ref` and `unknown-resolver`. `unconfigured` is the one a caller stays quiet
about, since a keyless host is a legitimate configuration. `/doctor` checks a credential
with `_secret_resolve.py --probe <NAME>`, which prints only the category to stderr and
never writes stdout, instead of importing the helper or running `op read` itself.

What unavailability means is the caller's decision. As shipped, every asset's `get_key()`
is the same (`sys.exit(str(e))`, a non-zero exit with the redacted message), and the
fatal-versus-floor split lives in each command's handler doc: `linear-common.md` floors
the three read fast paths, while `linear-archive.md` and `linear-false-closures.md` treat
the exit as fatal. The design, `dev_docs/designs/2026-08-01-auth-key-resolution.md` (deleted on graduation), had placed that split in each script's `get_key()`.

## Consequences

- Good, because redaction is enforced in code, so direct invocation and logs cannot leak
  the reference.
- Good, because a caller can give an actionable line per category, and stays silent for
  a keyless host.
- Bad, because `_classify_stderr` maps resolver output to `no-session` or `not-found` by
  substring and calls anything else `denied`, so a resolver whose wording changes is
  misreported as `denied`.
- Bad, because the fatal-versus-floor behaviour rests on handler prose, not code.

## Revisit when

- A resolver's failure output stops matching the substrings `_classify_stderr` looks for.
- A new consumer needs the fatal-versus-floor decision enforced in code.

## Confirmation

`scripts/test_secret_resolve.py`: `test_full_ref_never_in_error_message`, the
classification tests, `test_unconfigured_host_is_its_own_category`, and the `--probe`
tests (`test_success_is_silent_on_stdout`, `test_failure_reports_only_the_category`).
The fatal-versus-floor split rests on the handler docs alone.

## Alternatives

- **Exit status only:** rejected because it cannot tell a lapsed session from a missing
  item.
- **Eight categories, with "nothing configured" sharing `not-found`:** the first draft;
  replaced by a separate `unconfigured` during implementation, because a keyless host
  must floor silently while a configured but unresolvable key gets an explanatory line.
- **A flat "non-fatal, floors" for every consumer:** rejected because the archive and
  false-closures commands have no MCP floor.
