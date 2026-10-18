Fixture inputs for `test/lint-pipefail.bats`.

These carry deliberate `| grep -q` pipelines, so they are `.txt` on purpose: the
tree-wide scan in `scripts/lint-shell.sh` only walks `*.sh`, `*.bash`, `*.bats`,
and these must not fail it. `scripts/lint-pipefail.sh` lints whatever paths it
is handed, so the extension costs the test nothing.

Rename one to `.sh` and `just check` starts failing on fixtures — that is the
extension doing its job, not a bug.
