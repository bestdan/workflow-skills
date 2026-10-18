# `dev_docs/` in this repo

The layout follows `dev_docs_layout.md` at the root of the `bestdan/agent-guidance`
plugin: a date in the filename means the file is a record, no date means it is
live and kept current. This file lists the directories this repo has and the
exceptions it makes; it does not restate the convention.

`scripts/dev-docs-layout.sh` runs the plugin's checker over this directory as
part of `just check`.

| Path                  | Holds                                                                         |
| --------------------- | ----------------------------------------------------------------------------- |
| `<topic>.md`          | conventions and runbooks — how a subsystem works, how to release, how to test |
| `designs/`            | proposals for changes not yet made                                            |
| `decisions/`          | why a choice was made and what would reopen it                                |
| `research/`           | evidence gathered to answer a question, including run and review records      |
| `workflow-review/`    | recurring reviews of the task workflow as a whole                             |
| `tasks/`              | `.task-config.yml` and `/plan-with-docs` scaffolding                          |
| `.handoffs/`          | notes from one session to the next                                            |
| `co-review/`          | `/co-review`'s machine-local config                                           |
| `orchestrate-coders/` | `/orchestrate-coders`' machine-local config                                   |

## Exceptions

- **`tasks/` is ignored except `.task-config.yml`.** This repo's handler is
  `gh-issue`, so GitHub owns task state and plan scaffolding stays local. The
  reasoning is in the `.gitignore` comment block.
- **`.handoffs/` is ignored except its `README.md`**, which is force-added.
  Every note in it stays untracked, and that asymmetry is deliberate — see
  [`.handoffs/README.md`](.handoffs/README.md).
- **`co-review/` and `orchestrate-coders/`** are the machine-local config
  directories the layout allows one per skill, both ignored.
