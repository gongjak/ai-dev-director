"""적용 관문 — HEAD 프록시가 P2 만 남기고 통과시킨 결과도 적용할 수 있다 (2026-11-09).

전에는 관문이 reviewer_verdict == "pass" 만 받아서, 오케스트레이터가 PASS 로 끝낸 결과를
「원본에 적용」이 언제나 막았다."""
import json

import pytest

from core.result_manager import ResultManager


def _rm(tmp_path, review, decision, verdict):
    rm = ResultManager.__new__(ResultManager)
    run_dir = tmp_path / "RUN-X"
    run_dir.mkdir()
    if review is not None:
        (run_dir / "review.json").write_text(json.dumps(review), encoding="utf-8")
    if decision is not None:
        (run_dir / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    rm.run_dir = lambda run_id: run_dir
    return rm, {"reviewer_verdict": verdict}


def test_pass_review_is_accepted(tmp_path):
    rm, ev = _rm(tmp_path, None, None, "pass")
    assert rm._review_accepted("RUN-X", ev)


def test_minor_only_pass_is_accepted(tmp_path):
    rm, ev = _rm(tmp_path, {"verdict": "changes_required", "p0": [], "p1": [], "p2": ["x"]},
                 {"decision": "PASS"}, "changes_required")
    assert rm._review_accepted("RUN-X", ev)


@pytest.mark.parametrize("review,decision", [
    ({"verdict": "changes_required", "p0": ["boom"], "p1": [], "p2": ["x"]}, {"decision": "PASS"}),
    ({"verdict": "changes_required", "p0": [], "p1": ["bad"], "p2": ["x"]}, {"decision": "PASS"}),
    ({"verdict": "changes_required", "p0": [], "p1": [], "p2": []}, {"decision": "PASS"}),
    ({"verdict": "changes_required", "p0": [], "p1": [], "p2": ["x"]}, {"decision": "FAIL"}),
    (None, {"decision": "PASS"}),
    ({"verdict": "changes_required", "p0": [], "p1": [], "p2": ["x"]}, None),
])
def test_other_cases_are_refused(tmp_path, review, decision):
    rm, ev = _rm(tmp_path, review, decision, "changes_required")
    assert not rm._review_accepted("RUN-X", ev)


def test_missing_or_unknown_verdict_is_refused(tmp_path):
    rm, ev = _rm(tmp_path, {"verdict": "changes_required", "p0": [], "p1": [], "p2": ["x"]},
                 {"decision": "PASS"}, None)
    assert not rm._review_accepted("RUN-X", ev)


def test_deletion_warning_in_p2_is_refused(tmp_path):
    rm, ev = _rm(tmp_path, {"verdict": "changes_required", "p0": [], "p1": [], "p2": ["Removed old helper"]},
                 {"decision": "PASS"}, "changes_required")
    assert not rm._review_accepted("RUN-X", ev)


def test_broken_json_is_refused(tmp_path):
    rm, ev = _rm(tmp_path, None, {"decision": "PASS"}, "changes_required")
    (tmp_path / "RUN-X" / "review.json").write_text("{broken", encoding="utf-8")
    assert not rm._review_accepted("RUN-X", ev)
