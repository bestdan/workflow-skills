Fixture inputs for `test/lint-bash4.bats`.

These carry deliberate bash-4+ constructs, so they are `.txt` on purpose: the
tree-wide scan in `scripts/lint-shell.sh` only walks `*.sh`, `*.bash`, `*.bats`,
and these must not fail it. `scripts/lint-bash4.sh` lints whatever paths it is
handed, so the extension costs the test nothing.

Rename one to `.sh` and `just check` starts failing on fixtures — that is the
extension doing its job, not a bug.
