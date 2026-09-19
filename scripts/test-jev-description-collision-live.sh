#!/usr/bin/env bash
# test-jev-description-collision-live.sh — opt-in live smoke against the Jev API.
#
# Excluded from `just check` by check.sh's test-*-live.sh rule, and aggregated by
# scripts/test-shell-live.sh. It is excluded for two reasons, not one: it needs the
# network, and it spends money — roughly $0.0001 for the single request below.
#
# Skips cleanly (exit 0) when no key is configured, so the live aggregator stays
# green on a machine that has never held a TypeSafe key. A key that IS present and
# does not work is a failure, not a skip: that is the case worth catching.
#
# Run directly: bash scripts/test-jev-description-collision-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

if ! python3 - <<'PY'; then
import importlib.util, os, pathlib, sys
root = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location(
    "j", root / "scripts" / "jev-description-collision.py")
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)
if os.environ.get("TYPESAFE_API_KEY"):
    sys.exit(0)
for p in j.local_config_paths(root):
    if j.extract_key(p.read_text()):
        sys.exit(0)
sys.exit(1)
PY
  echo "test-jev-description-collision-live: SKIP (no TypeSafe key configured)"
  exit 0
fi

# One request, one prompt, against the real option set. Asserts the response shape
# the research record documents — a winner drawn from the option set, a probability
# map that sums to about 1, and a confidence — rather than any particular winner,
# which would make this a model-behaviour test and turn it flaky on a model bump.
python3 - <<'PY'
import importlib.util, pathlib, sys

root = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location(
    "j", root / "scripts" / "jev-description-collision.py")
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)

key = j.resolve_key(root)
criteria = j.load_descriptions(root)
data = j.ask(key, "Walk me through it so I actually get it.", criteria)

answer = data["answers"]["skill"]
probs = answer["probabilities"]
problems = []
if answer.get("type") != "choice":
    problems.append(f"type was {answer.get('type')!r}, expected 'choice'")
if answer.get("choice") not in criteria:
    problems.append(f"choice {answer.get('choice')!r} is not in the option set")
if set(probs) != set(criteria):
    problems.append("probabilities do not cover exactly the option set")
if not 0.98 <= sum(probs.values()) <= 1.02:
    problems.append(f"probabilities sum to {sum(probs.values()):.4f}, not ~1")
if not isinstance(answer.get("confidence"), (int, float)):
    problems.append("confidence missing or not numeric")

winner, p_win, runner, p_run, margin = j.rank(probs)
if problems:
    for p in problems:
        print(f"  FAIL {p}", file=sys.stderr)
    sys.exit(1)
print(f"  winner={winner} {p_win:.3f}  runner_up={runner} {p_run:.3f} "
      f"margin={margin:.3f} conf={answer['confidence']:.2f}")
print(f"  usage={data.get('usage')}")
PY

echo "test-jev-description-collision-live: OK"
