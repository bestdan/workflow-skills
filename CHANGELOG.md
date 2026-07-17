# Changelog

All notable changes to this plugin. Sections are auto-generated from
[Conventional Commits](https://www.conventionalcommits.org/) on merge to
`main` by `.github/workflows/release.yml`.

## [1.62.0] - 2026-07-17

### Features

- validate reset_epoch and add resume grace at the pause writer (#226) (e6eed6a)

## [1.61.0] - 2026-07-17

### Features

- pre-invoke reserve gate before Claude-heavy turns (#225) (2d0a3d0)

## [1.60.0] - 2026-07-17

### Features

- add CAO coder backend (cao-coder.sh) (#224) (b03bfd8)

## [1.59.0] - 2026-07-15

### Features

- guard review fixes against a PR merged mid-flow (#220) (5b7d695)

## [1.58.0] - 2026-07-15

### Features

- add car overnight auto-resume launcher (#221) (1061828)

## [1.57.0] - 2026-07-15

### Features

- add Linear false-closures detection backstop (#218) (a3581d2)

## [1.56.1] - 2026-07-14

### Fixes

- restore the operational-modifiers table, harden the auth probe (#215) (4cbeac0)

## [1.56.0] - 2026-07-14

### Features

- add secret-exposure, containment, and context dimensions (#214) (9816c87)

## [1.55.2] - 2026-07-14

### Fixes

- deliver agy's input in -p, not on stdin (#213) (532fa6a)

## [1.55.1] - 2026-07-13

### Fixes

- reap the watchdog's sleep, which was holding $( ) open (#211) (122efb8)

## [1.55.0] - 2026-07-13

### Features

- teach the work until the human can defend it (#210) (d9a60ad)

## [1.54.2] - 2026-07-13

### Fixes

- grant the jail the binaries it was only pretending to have (#208) (436555d)

## [1.54.1] - 2026-07-13

### Fixes

- keep the supervisor ledger out of git (#200) (cc8d8fe)

## [1.54.0] - 2026-07-13

### Features

- ship the periodic status report the operator had to hand-roll (task 20) (#199) (c9843a8)

## [1.53.4] - 2026-07-12

### Fixes

- give the pause exemption an authority the agent cannot forge (task 23) (#196) (b825dd0)

## [1.53.3] - 2026-07-12

### Fixes

- doctor I5 fails closed when a worktree's git reads fail (task 24) (#195) (1d8c2b9)

## [1.53.2] - 2026-07-12

### Fixes

- thread the supervisor thresholds through the launch path (task 25) (#197) (a6f69e7)

## [1.53.1] - 2026-07-12

### Fixes

- subshell the die-capable teardown calls the halt path swallows with `|| true` (task 26) (#193) (3dc471d)

## [1.53.0] - 2026-07-12

### Features

- run doctor — assert the run's own invariants every iteration (task 14) (#189) (433c0e6)

## [1.52.0] - 2026-07-12

### Features

- exit contract + heartbeat — distinguish "paused mid-run" from "run complete" (task 15) (#190) (0b1c5c5)

## [1.51.0] - 2026-07-12

### Features

- alarm channel — a halted or stalled run must tell a human (task 16) (#191) (c16f2cc)

## [1.50.0] - 2026-07-12

### Features

- gate the supervisor relaunch on paused_until in shell (task 11) (#188) (ca14c74)

## [1.49.1] - 2026-07-12

### Fixes

- the harness's /tmp tree is claude-<N>, and N is not the uid (#187) (87a3486)

## [1.49.0] - 2026-07-11

### Features

- automate the post-merge restack of stacked PRs (task 18) (#184) (c0ce0ed)

## [1.48.0] - 2026-07-11

### Features

- supervisor halts on a fatal auth failure instead of relaunching forever (task 10) (#183) (a4bb80e)

## [1.47.2] - 2026-07-11

### Fixes

- permit the harness's own $TMPDIR runtime files — stop poisoning every Bash exit code (task 12) (#182) (65e4008)

## [1.47.1] - 2026-07-11

### Fixes

- the run worktree's HEAD never leaves the run-state branch (task 13) (#181) (85bb0d9)

## [1.47.0] - 2026-07-11

### Features

- route cross-cutting/round-bound co-review deferrals to /add-task (task_7, P4) (#175) (661ea6b)

## [1.46.0] - 2026-07-11

### Features

- status subcommand + done-sentinel; Stop-hook mitigation (task_8, P5) (#176) (45cdd95)

## [1.45.0] - 2026-07-11

### Features

- scripts/preflight.sh — one read-only launch pre-flight (task_5, P2 #7/#10) (#173) (d25f4f6)

## [1.44.0] - 2026-07-11

### Features

- verify broker — run the run verify OUTSIDE the jail (task_4, P1 #4/#6) (#172) (c74fda1)

## [1.43.0] - 2026-07-11

### Features

- profile write-scopes — credential files RO vs tool state dirs RW (task_3, P1 #5) (#177) (f1b6fb6)

## [1.42.0] - 2026-07-11

### Features

- render-profile toolchain-exec mode (task_2, P0 #3) (#170) (34b0325)

## [1.41.1] - 2026-07-11

### Fixes

- spawn-orchestrator launch works end-to-end (--verbose + PATH) (#169) (bc7acc9)

## [1.41.0] - 2026-07-11

### Features

- wire .task-config.local.yml override so api_key_ref resolves (PRE-500) (#168) (bd047a5)

## [1.40.0] - 2026-07-11

### Features

- linear-relations.py GraphQL fast-path for reoptimize + smoke test (#166) (3521632)

## [1.39.0] - 2026-07-11

### Features

- linear-scan.py GraphQL fast-path for the in-flight scan + smoke test (#163) (29b29e3)

## [1.38.1] - 2026-07-11

### Fixes

- assignee fallback, robust by-id team resolve, docstrings (#161) (ff7cada)

## [1.38.0] - 2026-07-10

### Features

- Step-7 spawn mechanism — detach + launchd + jail generator (PRE-484 task 3) (3864082)

## [1.37.0] - 2026-07-10

### Features

- layer-2 network egress allowlist emitter (PRE-484 task 2) (#153) (c8f38be)

## [1.36.0] - 2026-07-10

### Features

- seatbelt FS/exec profile generator for Step 7 spawn (PRE-484 task 1) (#151) (4b3c4ef)

## [1.35.0] - 2026-07-10

### Features

- --handler override, set by auto-pilot from source (PRE-482) (#149) (eb6c84e)

## [1.34.0] - 2026-07-09

### Features

- [PRE-465] --resume phase — crash reconciliation into the run loop (#146) (4a33150)

## [1.33.0] - 2026-07-09

### Features

- run phase — orchestrator loop, budget bounds, pause/wake (PRE-464) (#145) (431493d)

## [1.32.1] - 2026-07-09

### Fixes

- don't declare unused $project var in whole-team query (#143) (badc17c)

## [1.32.0] - 2026-07-09

### Features

- review half — PR, co-review, iterate, hand-off (PRE-460) (#140) (072614b)

## [1.31.0] - 2026-07-09

### Features

- /deliver-task core — claim + do half (PRE-459) (#138) (0cc11ef)

## [1.30.0] - 2026-07-09

### Features

- add --non-interactive mode for unattended runs (#135) (a8a64e6)

## [1.29.0] - 2026-07-07

### Features

- add GitHub Copilot CLI as a built-in co-reviewer (#132) (2243993)

## [1.28.0] - 2026-07-06

### Features

- add staleness pre-flight so reviews never run on stale local state (#129) (76acb0b)

## [1.27.0] - 2026-07-04

### Features

- scope claim WIP per-project with an Unassigned bucket (#121) (4273c20)

## [1.26.2] - 2026-07-04

### Fixes

- harden agy/devin auth with pre-flight probes; consolidate with select-coder (#115) (b99301e)

## [1.26.1] - 2026-07-04

### Fixes

- resolve local-reviewer config from the main working tree (#117) (fa14e80)

## [1.26.0] - 2026-07-03

### Features

- extract task profiling into a standalone /assess-task skill (#116) (b62400f)

## [1.25.0] - 2026-07-03

### Features

- add /reoptimize-tasks command to audit and repair tracker backlogs (#83) (ab3d727)

## [1.24.0] - 2026-07-03

### Features

- add agent/model selector skill and /select-coder command (#114) (8568e04)

## [1.23.0] - 2026-07-03

### Features

- add coder-orchestration skill and /orchestrate-coders command (#107) (0dc90ac)

## [1.22.7] - 2026-07-03

### Fixes

- scope is_blocked_by validation per handler (#113) (af4850c)

## [1.22.6] - 2026-07-03

### Fixes

- sweep verified doc drift across five handler/config files (#112) (1694db4)

## [1.22.5] - 2026-07-03

### Fixes

- standardize model_output.json and dedupe marimo guidance (#111) (cdbad4a)

## [1.22.4] - 2026-07-03

### Fixes

- make the audit protocol executable by a subagent (#110) (47de900)

## [1.22.3] - 2026-07-03

### Fixes

- run cheap in-flight pre-flight before feasibility judge in gh-issue and jira claim flows (#109) (45567ff)

## [1.22.2] - 2026-07-03

### Fixes

- align trigger threshold and slice budget with the shared size scale (#108) (f372c8d)

## [1.22.1] - 2026-07-03

### Fixes

- make eval.sh timeout portable on macOS (#76) (c9675d2)

## [1.22.0] - 2026-07-03

### Features

- gitignore the whole dev_docs/co-review folder on setup (#106) (90dc4e1)

## [1.21.0] - 2026-07-02

### Features

- add Devin CLI as a built-in local reviewer (#105) (bdb5bb2)

## [1.20.0] - 2026-07-01

### Features

- migrate plan overview to the tracker, then delete local files (#95) (86a5ff4)

## [1.19.0] - 2026-07-01

### Features

- [PRE-334] migrate archive sweep + push-plan targeting to configured-projects list (#102) (a24aaf5)

## [1.18.0] - 2026-07-01

### Features

- [PRE-333] migrate selection prompts to configured-projects list (#101) (66073d4)

## [1.17.0] - 2026-07-01

### Features

- [PRE-332] /do-tasks per-project candidate query + per-project/global WIP (#103) (3de46bd)

## [1.16.0] - 2026-07-01

### Features

- [PRE-331] /task-config multi-project setup + scalar→list migration (#100) (dc9e7b4)

## [1.15.0] - 2026-06-30

### Features

- [PRE-330] projects-list schema + resolve-configured-projects helper (#99) (d596f79)

## [1.14.0] - 2026-06-30

### Features

- add Google Antigravity (agy) as a built-in reviewer (#98) (03d9e97)

## [1.13.1] - 2026-06-30

### Fixes

- close the Linear claim race with a token-comment lock (#96) (283c312)

## [1.13.0] - 2026-06-29

### Features

- add shared await-pr-review bot-watcher fixture (#90) (8cb11a3)

## [1.12.0] - 2026-06-28

### Features

- add /archive-tasks handler-dispatched verb (cadb084)

### Fixes

- apply co-review fixes (b56d009)

## [1.11.0] - 2026-06-25

### Features

- add GraphQL parent-rollup detection to promote handler (58ddd68)

### Fixes

- honor configured repo in parent-rollup GraphQL query (b62487a)

## [1.10.6] - 2026-06-25

### Fixes

- push claim label filter server-side (PRE-129) (514bbdb)

## [1.10.5] - 2026-06-24

### Fixes

- scope --no-claim pre-flight and harden title search (e2400f1)
- pre-flight in-flight PRs/branches and claim-then-verify at start of work (f17a685)

## [1.10.4] - 2026-06-24

### Fixes

- pre-flight in-flight PRs/branches and claim-then-verify at start of work (e0d3986)

## [1.10.3] - 2026-06-19

### Fixes

- retire gemini as a built-in reviewer (2ac0359)

## [1.10.2] - 2026-06-16

### Fixes

- skip dev_docs/tasks/ exclude for repo-pr handler (5facd65)
- make exclude appends idempotent and pass CI (8899bae)

## [1.10.1] - 2026-06-14

### Fixes

- prevent bare sibling issue IDs in PR bodies from auto-closing unrelated tasks (cc0e50d)

## [1.10.0] - 2026-06-14

### Features

- scope /promote-tasks to one project/epic/milestone by default (19f07c2)

### Fixes

- resolve pinned Linear project name for the scope report (b9f3663)

## [1.9.1] - 2026-06-11

### Fixes

- request Flagged field on direct-key claim lookup (252ff6a)
- exclude blocked issues from promote/claim candidates (2ceca5a)

## [1.9.0] - 2026-06-10

### Features

- add jira claim/execute split + pre-claim WIP gate (5352747)

### Fixes

- disambiguate claim transition when multiple In-Progress statuses exist (86bc792)
- correct WIP-gate JQL facts and gloss <base> in --no-claim (e0602e5)
- dprint formatting and invalid statusCategory JQL in jira WIP gate (8a55d49)

## [1.8.0] - 2026-06-10

### Features

- single /do-tasks execute path (jira-claim.md) (700b3fc)

### Fixes

- harden jira-claim race, base-branch, and bail-label steps (8169d95)

## [1.7.0] - 2026-06-08

### Features

- dynamic transition resolution + prompt-when-unset for /promote-tasks (bb8ca0b)

## [1.6.2] - 2026-06-08

### Fixes

- keep reviewer dispatch within documented matcher contract (ba0f27c)
- assemble reviewer input in one shell to avoid stale cross-sandbox temp reads (a6ab6e2)

## [1.6.1] - 2026-06-08

### Fixes

- scope linear WIP gate count by project (d75bc3e)

## [1.6.0] - 2026-06-08

### Features

- add /promote-tasks support against configured statuses (a02a4c5)

### Fixes

- resolve promote target status to a transition id (57847d3)
- correct self-defeating promote example and tighten docs (cf14483)

## [1.5.0] - 2026-06-08

### Features

- add claim/execute split + pre-claim WIP gate (b269cd4)

### Fixes

- resolve co-review findings on claim/execute split (cdde3e7)

## [1.4.0] - 2026-06-08

### Features

- add gh-issue single-execute path (9b22f67)

### Fixes

- make find/claim/branch steps implementable (de685b7)

## [1.3.0] - 2026-06-08

### Features

- add gh-issue promote handler (c2cb23e)

### Fixes

- exclude scored issues at query time (ddb4b0c)
