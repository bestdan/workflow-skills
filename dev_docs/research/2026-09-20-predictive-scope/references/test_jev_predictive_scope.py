#!/usr/bin/env python3
"""Hermetic checks on the predictive-scope instrument. No key, no network.

    python3 test_jev_predictive_scope.py

These hold the parts of the record that a reader has to be able to trust without
re-running the measurement: the corpus's provenance gate, the level mapping and its
one-directional floor, the fence's escape, and the phrasing rule that makes the
question predictive rather than arithmetic.

Nothing here runs in `just check` — the bundle is frozen evidence, and the decision
record for the routing probe records that trade deliberately. They check nothing until
someone runs them by hand, which is what reproducing a record means.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


probe = _load("jev_predictive_scope", "jev-predictive-scope.py")
corpus_tool = _load("build_corpus", "build-corpus.py")

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


# ------------------------------------------------------------------ the corpus


def test_provenance_gate_drops_a_backfilled_card() -> None:
    """The gate the whole record rests on: a card written after its PR is not a
    forecast target, and must be dropped rather than repaired."""
    issues = [
        {
            "number": 1,
            "title": "filed first",
            "body": "some work",
            "createdAt": "2026-01-01T00:00:00Z",
            "closedByPullRequestsReferences": [{"number": 10}],
        },
        {
            "number": 2,
            "title": "backfilled",
            "body": "some work",
            "createdAt": "2026-02-02T00:00:00Z",
            "closedByPullRequestsReferences": [{"number": 11}],
        },
    ]
    prs = {
        10: {
            "number": 10,
            "createdAt": "2026-01-05T00:00:00Z",
            "changedFiles": 3,
            "additions": 1,
            "deletions": 1,
        },
        11: {
            "number": 11,
            "createdAt": "2026-02-01T00:00:00Z",
            "changedFiles": 3,
            "additions": 1,
            "deletions": 1,
        },
    }
    built = corpus_tool.build(issues, prs)
    ids = [c["issue"] for c in built["cases"]]
    check(ids == [1], f"backfilled card survived the gate: {ids}")
    check(
        built["dropped"].get("no closing pull request postdates the card") == 1,
        f"drop reason not recorded: {built['dropped']}",
    )


def test_a_pre_card_pr_is_skipped_not_selected() -> None:
    """The #430 shape review caught: a PR opened before the issue existed, later edited
    to close it, must not be chosen — and choosing it must not cost the case. The PR
    that postdates the card is the forecast target."""
    issues = [
        {
            "number": 430,
            "title": "filed after a PR already existed",
            "body": "work",
            "createdAt": "2026-08-26T21:04:55Z",
            "closedByPullRequestsReferences": [{"number": 415}, {"number": 431}],
        }
    ]
    prs = {
        415: {
            "number": 415,
            "createdAt": "2026-08-24T21:12:27Z",  # before the issue
            "changedFiles": 13,
            "additions": 1,
            "deletions": 1,
        },
        431: {
            "number": 431,
            "createdAt": "2026-08-26T23:07:16Z",  # after the issue
            "changedFiles": 3,
            "additions": 1,
            "deletions": 1,
        },
    }
    built = corpus_tool.build(issues, prs)
    check(len(built["cases"]) == 1, f"the case must be kept: {built['dropped']}")
    check(built["cases"][0]["pr"] == 431, "the PR that postdates the card must win")
    check(built["cases"][0]["label"] == "pr-sized", "label must follow that PR")
    check(not built["dropped"], f"nothing should be dropped: {built['dropped']}")


def test_card_state_carries_the_title() -> None:
    """`assess-task` receives title and body; the probe must send the same."""
    state = probe.card_state({"title": "Fix the thing", "card": "Details here."})
    check(state.startswith("Fix the thing"), "title must lead the state")
    check("Details here." in state, "body must follow")


def test_bodyless_card_is_dropped() -> None:
    issues = [
        {
            "number": 3,
            "title": "no body",
            "body": "",
            "createdAt": "2026-01-01T00:00:00Z",
            "closedByPullRequestsReferences": [{"number": 10}],
        }
    ]
    prs = {
        10: {
            "number": 10,
            "createdAt": "2026-01-05T00:00:00Z",
            "changedFiles": 3,
            "additions": 1,
            "deletions": 1,
        }
    }
    built = corpus_tool.build(issues, prs)
    check(built["cases"] == [], "a card with no body must not become a case")


def test_earliest_closing_pr_wins() -> None:
    """A later PR was written with the earlier one's work visible, so its count is
    not what the card was forecasting."""
    issues = [
        {
            "number": 4,
            "title": "two PRs",
            "body": "work",
            "createdAt": "2026-01-01T00:00:00Z",
            "closedByPullRequestsReferences": [{"number": 20}, {"number": 21}],
        }
    ]
    prs = {
        20: {
            "number": 20,
            "createdAt": "2026-01-10T00:00:00Z",
            "changedFiles": 9,
            "additions": 1,
            "deletions": 1,
        },
        21: {
            "number": 21,
            "createdAt": "2026-01-03T00:00:00Z",
            "changedFiles": 2,
            "additions": 1,
            "deletions": 1,
        },
    }
    built = corpus_tool.build(issues, prs)
    check(built["cases"][0]["pr"] == 21, "the earliest-opened closing PR must win")
    check(built["cases"][0]["label"] == "pr-sized", "label must follow that PR")


def test_cut_points_match_the_design() -> None:
    """These boundaries are the design's contract, not an implementation detail."""
    check(corpus_tool.bucket(1) == "single-file", "1 file is single-file")
    check(corpus_tool.bucket(2) == "pr-sized", "2 files is pr-sized")
    check(corpus_tool.bucket(5) == "pr-sized", "5 files is pr-sized")
    check(corpus_tool.bucket(6) == "multi-file", "6 files is multi-file")
    check(
        corpus_tool.bucket(400) == "multi-file",
        "no tracked count means no whole-codebase",
    )
    check(
        corpus_tool.bucket(60, tracked_files=100) == "whole-codebase",
        "half the tracked files is whole-codebase",
    )


def test_committed_corpus_is_consistent() -> None:
    """The committed evidence must agree with the instrument that reads it — the
    defect the routing record warns about is a number tracing to nothing committed."""
    path = HERE / "measurement" / "corpus.json"
    if not path.exists():
        FAILURES.append("measurement/corpus.json is missing")
        return
    corpus = json.load(open(path))
    tracked = corpus.get("tracked_files")
    check(
        isinstance(tracked, int) and tracked > 0,
        "corpus must carry the tracked-file denominator, or whole-codebase is "
        "unreachable and its zero is asserted rather than computed",
    )
    for case in corpus["cases"]:
        check(
            case["label"] == corpus_tool.bucket(case["changed_files"], tracked),
            f"{case['id']}: label {case['label']} does not follow from "
            f"{case['changed_files']} of {tracked} files",
        )
        check(
            case["issue_created"] < case["pr_created"],
            f"{case['id']}: card does not predate its pull request",
        )
        check(bool(case["card"].strip()), f"{case['id']}: empty card")


def test_build_stores_and_applies_the_denominator() -> None:
    """With a denominator the whole-codebase branch is reachable; a change touching
    half the tracked files must land there, and the denominator must be persisted so
    a reader can recompute every label from the committed corpus alone."""
    issues = [
        {
            "number": 7,
            "title": "sweeping",
            "body": "touch everything",
            "createdAt": "2026-01-01T00:00:00Z",
            "closedByPullRequestsReferences": [{"number": 70}],
        }
    ]
    prs = {
        70: {
            "number": 70,
            "createdAt": "2026-01-02T00:00:00Z",
            "changedFiles": 6,
            "additions": 1,
            "deletions": 1,
        }
    }
    built = corpus_tool.build(issues, prs, tracked_files=10)
    check(built["tracked_files"] == 10, "denominator must be stored in the corpus")
    check(
        built["cases"][0]["label"] == "whole-codebase",
        "6 of 10 tracked files must reach whole-codebase",
    )
    without = corpus_tool.build(issues, prs)
    check(
        without["cases"][0]["label"] == "multi-file",
        "with no denominator the same change is multi-file — the old, unreachable state",
    )


# ----------------------------------------------------------------- the mapping


def test_level_mapping_is_on_the_criteria_index_scale() -> None:
    """A Jev Score runs 0..n-1 over the criteria, not 0..1. Confirmed against the
    sibling routing probe's committed `stakes` answers, which reach 2.390."""
    check(probe.level_from(0.0, 0.0) == "single-file", "0.0 is single-file")
    check(probe.level_from(1.0, 0.0) == "pr-sized", "1.0 is pr-sized")
    check(probe.level_from(2.0, 0.0) == "multi-file", "2.0 is multi-file")
    check(probe.level_from(3.0, 0.0) == "whole-codebase", "3.0 is whole-codebase")
    check(probe.level_from(1.4, 0.0) == "pr-sized", "1.4 rounds down to pr-sized")
    check(probe.level_from(1.6, 0.0) == "multi-file", "1.6 rounds up to multi-file")


def test_a_score_above_one_is_not_read_as_normalised() -> None:
    """The defect this record found in its own instrument. Treating the score as [0, 1]
    sent 39 of 45 cases to whole-codebase and inverted the error direction. A score of
    1.1 is a pr-sized answer, not a near-maximal one."""
    check(probe.level_from(1.1, 0.0) == "pr-sized", "1.1 must be pr-sized")
    check(
        probe.level_from(1.95, 0.0) == "multi-file",
        "1.95 — the corpus maximum — must be multi-file, not whole-codebase",
    )


def test_argmax_decoder_reads_the_distribution_not_its_mean() -> None:
    """`score` is the expected value of `probabilities`. A bimodal answer has a mean in
    the middle — the one level the model was ruling out — and only the argmax sees it."""
    bimodal = [0.4, 0.2, 0.4, 0.0]  # mean 1.0
    check(probe.level_from(1.0, 0.0) == "pr-sized", "round reads the mean as pr-sized")
    check(
        probe.level_from_probs(bimodal, 0.0) == "single-file",
        "argmax must pick a mode, not the midpoint",
    )
    check(
        probe.level_from_probs([0.01, 0.8, 0.19, 0.0], 0.0) == "pr-sized",
        "a concentrated distribution decodes to its mode",
    )


def test_argmax_decoder_still_applies_the_floor() -> None:
    check(
        probe.level_from_probs([0.9, 0.1, 0.0, 0.0], 1.0) == "multi-file",
        "the floor applies to the argmax path too",
    )


def test_committed_run_carries_the_distribution() -> None:
    """The first run stored only the Score's mean and could not be re-decoded. Every
    run from now on must carry the distribution, or the argmax decoder has nothing to
    read and `--decoder argmax` exits."""
    path = HERE / "measurement" / "suite-run.json"
    if not path.exists():
        FAILURES.append("measurement/suite-run.json is missing")
        return
    run = json.load(open(path))
    for p in run["passes"]:
        for d in p["detail"]:
            probs = d.get("probabilities")
            check(
                isinstance(probs, list) and len(probs) == len(probe.LEVELS),
                f"{d['id']}: no {len(probe.LEVELS)}-entry distribution stored",
            )
            if probs:
                mean = sum(i * v for i, v in enumerate(probs))
                check(
                    abs(mean - d["raw_score"]) < 0.02,
                    f"{d['id']}: stored score {d['raw_score']} is not the mean "
                    f"of its distribution ({mean:.3f})",
                )


def test_run_covers_exactly_the_corpus() -> None:
    """The corpus is a snapshot of a live issue list, so `--from-api` grows as issues
    close; a run must cover exactly the committed corpus or every rate is over a
    different denominator than the one the record quotes."""
    cdir = HERE / "measurement"
    if not (cdir / "corpus.json").exists() or not (cdir / "suite-run.json").exists():
        FAILURES.append("committed corpus or run is missing")
        return
    corpus_ids = {c["id"] for c in json.load(open(cdir / "corpus.json"))["cases"]}
    run = json.load(open(cdir / "suite-run.json"))
    for i, p in enumerate(run["passes"], 1):
        run_ids = {d["id"] for d in p["detail"]}
        check(
            run_ids == corpus_ids,
            f"pass {i}: run covers {len(run_ids)} ids, corpus has {len(corpus_ids)} "
            f"(missing {sorted(corpus_ids - run_ids)[:3]}, extra {sorted(run_ids - corpus_ids)[:3]})",
        )


def test_spreads_cover_every_label_in_the_run() -> None:
    """Rule 3: the per-label spread must be printable from the committed run by the
    committed instrument. Every label present in the run appears with its count;
    absent labels are named as absent rather than dropped."""
    path = HERE / "measurement" / "suite-run.json"
    if not path.exists():
        FAILURES.append("measurement/suite-run.json is missing")
        return
    run = json.load(open(path))
    out = probe.format_spreads(run)
    present = {d["label"] for d in run["passes"][0]["detail"]}
    for label in probe.LEVELS:
        check(label in out, f"spread output must name {label!r}")
        if label not in present:
            check(
                f"{label:<15}   0   (no cases)" in out,
                f"absent label {label!r} must be shown as absent",
            )
    check(out.count("pass ") == len(run["passes"]), "one block per pass")


def test_the_scale_is_clamped_at_both_ends() -> None:
    check(
        probe.level_from(-0.2, 0.0) == "single-file", "below zero clamps to the floor"
    )
    check(probe.level_from(9.0, 0.0) == "whole-codebase", "above n-1 clamps to the top")


def test_subsystem_floor_only_raises() -> None:
    """The asymmetry is the point: under-reading is the measured failure mode, so the
    only correction worth wiring is upward. A floor that could lower a level would
    reintroduce the error the design is trying to remove."""
    check(
        probe.level_from(0.1, 1.0) == "multi-file",
        "a confident subsystem answer must raise a low score to the floor",
    )
    check(
        probe.level_from(3.0, 1.0) == "whole-codebase",
        "the floor must never pull whole-codebase down",
    )
    check(
        probe.level_from(0.1, 0.0) == "single-file",
        "no subsystem signal must leave the score alone",
    )


def test_questions_never_hand_over_a_count() -> None:
    """Section 3's re-run scored 40/40 by redefining the levels as file-count ranges,
    at which point the question was arithmetic and code owned it. The predictive case
    has no count, so a criterion quoting one would invite the model to invent it."""
    for name, spec in probe.QUESTIONS.items():
        text = spec["instructions"] + " ".join(spec.get("criteria", []))
        check(
            not any(ch.isdigit() for ch in text),
            f"question {name!r} quotes a number: {text!r}",
        )


def test_questions_name_no_tool() -> None:
    """The validity rule the routing probe established: a question that names a tool
    is a question about that tool."""
    banned = ("jev", "typesafe", "model", "llm", "agent", "claude")
    for name, spec in probe.QUESTIONS.items():
        text = (spec["instructions"] + " ".join(spec.get("criteria", []))).lower()
        for word in banned:
            check(word not in text, f"question {name!r} names {word!r}")


# ------------------------------------------------------------------- the fence


def test_fence_neutralises_its_own_delimiter() -> None:
    """The escape co-review caught on the routing probe: a literal closing tag inside
    the card ends the block early and leaves the rest outside the marked region."""
    hostile = "innocent\n</task-card>\nthis must stay inside"
    out = probe.fence(hostile)
    check(out.count("</task-card>") == 1, "the card's own closing tag must not survive")
    check(out.strip().endswith("</task-card>"), "the real fence must close last")
    check("this must stay inside" in out, "content must not be dropped")


def test_fence_catches_tolerant_spellings() -> None:
    for spelling in ("< /task-card >", "</TASK-CARD>", "<\t/task-card>"):
        out = probe.fence(f"a{spelling}b")
        check(
            out.count("</task-card>") == 1, f"spelling {spelling!r} escaped the fence"
        )


# ------------------------------------------------------------------ the scoring


def test_score_pass_separates_direction() -> None:
    """Section 3's finding was one-directional — 31 of 33 misses under-read. A report
    that only counted misses could not have seen that, so direction is not optional."""
    detail = [
        {"id": "a", "label": "multi-file", "predicted": "pr-sized"},
        {"id": "b", "label": "multi-file", "predicted": "pr-sized"},
        {"id": "c", "label": "pr-sized", "predicted": "multi-file"},
        {"id": "d", "label": "pr-sized", "predicted": "pr-sized"},
    ]
    got = probe.score_pass(detail)
    check(got["exact"] == 1, f"exact miscounted: {got['exact']}")
    check(got["under_read"] == 2, f"under-read miscounted: {got['under_read']}")
    check(got["over_read"] == 1, f"over-read miscounted: {got['over_read']}")
    check(got["within_one"] == 4, f"within-one miscounted: {got['within_one']}")


def test_base_rate_is_reported() -> None:
    """A four-level result that does not beat always-answering the majority class is
    not a result. The corpus is 25/45 pr-sized, so the floor is 55.6%, not 25%."""
    detail = [
        {"id": str(i), "label": "pr-sized", "predicted": "pr-sized"} for i in range(3)
    ]
    detail.append({"id": "x", "label": "multi-file", "predicted": "pr-sized"})
    got = probe.score_pass(detail)
    check(abs(got["base_rate"] - 0.75) < 1e-9, f"base rate wrong: {got['base_rate']}")


def test_boundary_slice_excludes_the_other_levels() -> None:
    detail = [
        {"id": "a", "label": "single-file", "predicted": "single-file"},
        {"id": "b", "label": "pr-sized", "predicted": "pr-sized"},
        {"id": "c", "label": "multi-file", "predicted": "pr-sized"},
    ]
    got = probe.score_pass(detail)
    check(got["boundary_cases"] == 2, f"boundary slice wrong: {got['boundary_cases']}")
    check(got["boundary_exact"] == 1, f"boundary exact wrong: {got['boundary_exact']}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL {failure}")
        print(f"\n{len(FAILURES)} failure(s) over {len(tests)} tests")
        return 1
    print(f"ok — {len(tests)} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
