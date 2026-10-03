Fixture inputs for `test/lint-mktemp.bats`.

These carry deliberate bare `mktemp` calls, so they are `.txt` on purpose: the
tree-wide scan in `scripts/lint-shell.sh` only walks `*.sh`, `*.bash`, `*.bats`,
and these must not fail it.
