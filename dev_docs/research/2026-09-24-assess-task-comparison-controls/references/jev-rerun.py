#!/usr/bin/env python3
"""Re-run the typed call with questions-v2.json, and write a run file.

An artifact of dev_docs/research/2026-09-24-assess-task-comparison-controls/, not
standing tooling. A thin wrapper: everything but the question set is the 2026-09-22
record's own client (`jev-assess-task.py`) — the pinned model, the state rendering,
the decoders, the key ladder and the run-file format — so the only variable is the
questions. Needs a TypeSafe key (dev_docs/auth_key_access.md).

Run by path, from the repository root:

    D=dev_docs/research/2026-09-24-assess-task-comparison-controls/references
    python3 $D/jev-rerun.py --repeat 3 > $D/measurement/jev-run-v2.json
    O=dev_docs/research/2026-09-22-assess-task-comparison/references
    python3 $O/compare-assess-task.py --score $D/measurement/jev-run-v2.json \\
        $O/measurement/baseline/agent-1.json $O/measurement/baseline/agent-2.json \\
        $O/measurement/baseline/agent-3.json
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = (
    HERE.parents[1]
    / "2026-09-22-assess-task-comparison"
    / "references"
    / "jev-assess-task.py"
)
spec = importlib.util.spec_from_file_location("jev_assess_task", CLIENT)
jat = importlib.util.module_from_spec(spec)
sys.modules["jev_assess_task"] = jat
spec.loader.exec_module(jat)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--ids", metavar="A,B")
    args = p.parse_args()
    cases = json.loads(jat.CORPUS.read_text())["cases"]
    questions = jat.load_questions(HERE / "questions-v2.json")
    key = jat._jev.resolve_key(jat.ROOT)
    run = jat.run_suite(key, jat.select(cases, args.ids), args.repeat, questions)
    json.dump(run, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
