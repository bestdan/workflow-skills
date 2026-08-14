# Overnight auto-resume (`car`)

Anthropic's 5-hour usage cap kills the `claude` process outright, so nothing
inside an agent survives to relaunch itself. `scripts/claude-auto-resume.sh` is
an **external launcher** you run instead of `claude`: it runs the real binary,
and when the process exits it checks the live usage endpoint (via the bundled
`scripts/claude-usage.sh --session-status`) to tell "killed by the wall" apart
from "you quit". If capped, it sleeps until the reset time and resumes the same
conversation with `claude --continue`; otherwise it propagates the exit status
and stops. Needs zero setup beyond `claude-usage.sh` sitting next to it — no
statusline hook required.

Alias it once, then start overnight sessions with `car` instead of `claude`:

```sh
alias car='<plugin-dir>/scripts/claude-auto-resume.sh'
car
```

By default it re-execs itself inside a tmux session (`tmux new -A -s car`) so
the wait survives a closed terminal or dropped SSH — just run `car` and walk
away; reattach anytime with `tmux attach -t car`, or `car` again. Opt out with
`car --no-tmux` or `CAR_TMUX=0` (e.g. when scripting it, or when already in
tmux).

| Env var            | Default | What it controls                                                            |
| ------------------ | ------- | --------------------------------------------------------------------------- |
| `CAR_CAP_PCT`      | `100`   | Resume only when the live 5h usage window is at least this percent consumed |
| `CAR_BUFFER`       | `60`    | Seconds to wait past the reset time before resuming                         |
| `CAR_MAX_LOOPS`    | `12`    | Safety cap on the number of resumes before giving up                        |
| `CAR_TMUX`         | `1`     | `1` = self-wrap in tmux, `0` = never                                        |
| `CAR_TMUX_SESSION` | `car`   | tmux session name to create or reattach                                     |
