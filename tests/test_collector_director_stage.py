"""2단계 — Director 가 Collector 를 부른다(설계 §10). 네트워크 · 실제 CLI 없이 돈다.

검사기를 먼저 본다 — 지어낸 값 · 범위 밖 경로가 worktree 에 놓이지 않는지가 첫 시험이다.
"""
import csv
import json
import subprocess
from pathlib import Path

import pytest

from _collector_fakes import HTML, FakeTransport, fake_resolver, make_stub
from agents.codex_reviewer import CodexReviewer
from agents.mock_head import MockHead
from core import autopilot
from core.collector import providers, stage
from core.collector.netfetch import Fetcher
from core.collector.providers import HaikuCollector
from core.git_manager import GitManager
from core.orchestrator import Orchestrator
from core.state_machine import State, StateMachine
from schemas.order import OrderSchema
from tests.test_v0231_chat_stabilization import make_git_project

PYTEST_CMD = "python -m pytest tests/ -q"
URL = "https://example.com/m/2770"
PAGE = ("<html><head><title>매거진 2770</title></head><body><h3>구스테이크528</h3><p>서울특별시 강남구 압구정로 108</p>"
        "<h3>다른집</h3><p>서울 마포구 독막로 1</p></body></html>")
GOOD = {"claims": [{"target_id": "a", "field": "address", "value": "서울특별시 강남구 압구정로 108", "quote": "q"}]}
FAKE = {"claims": [{"target_id": "a", "field": "address", "value": "섬진강대로 2770", "quote": "q"}]}
CSV_PATH = "data/sources/web.csv"


def collect_spec(path=CSV_PATH, **over):
    spec = {"targets": [{"id": "a", "known": {"name": "구스테이크528", "sido": "서울"}, "fields": ["address"],
                         "urls": [URL]}],
            "output": {"schema": "steak-sources-v1", "path": path}}
    spec.update(over)
    return spec


def make_order(**over):
    data = {"goal": "식당 주소를 모아 지도 자료를 만든다", "allowed_paths": ["tests/", "src/", "data/"],
            "collect": collect_spec()}
    data.update(over)
    return OrderSchema(**data)


def fake_factory(tmp_path, monkeypatch, responses, routes=None, name="stage", **stub_kw):
    """수집 지시서를 받아 (가짜 전송 Fetcher, stub haiku) 를 돌려주는 주입구."""
    path, d = make_stub(tmp_path, name, responses, **stub_kw)
    monkeypatch.setattr(providers, "resolve_cli", lambda n: path)
    routes = {URL: (200, HTML, PAGE)} if routes is None else routes

    def factory(corder):
        f = Fetcher(corder.limits, corder.domains, transport=FakeTransport(routes), resolver=fake_resolver,
                    clock=lambda: 0.0, sleep=lambda s: None)
        return f, HaikuCollector(30, corder.limits.max_llm_calls)
    return factory, d


@pytest.fixture
def proj():
    ctx = make_git_project()
    p = ctx.__enter__()
    tests_dir = Path(p) / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_x.py").write_text("def test_1(): pass\ndef test_2(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "tests"], cwd=p, check=True, capture_output=True)
    try:
        yield p
    finally:
        ctx.__exit__(None, None, None)


def mock_orch(factory=None, **cfg_over):
    cfg = {"head": {"provider": "mock"}, "reviewer": {"provider": "mock"}, "verification": {"test_command": PYTEST_CMD}}
    cfg.update(cfg_over)
    orch = Orchestrator(config=cfg, head_agent=MockHead(), reviewer_agent=CodexReviewer(provider="mock"))
    orch._collector_factory = factory
    return orch


def adding(seen=None):
    def builder(b, o, w):
        if seen is not None:
            p = Path(w) / CSV_PATH
            seen.append(p.read_text(encoding="utf-8") if p.exists() else None)
        (Path(w) / "tests" / "test_y.py").write_text("def test_3(): pass\n", encoding="utf-8")
        return ["tests/test_y.py"]
    return builder


def states(orch, run_id):
    return [t["to_state"] for t in orch.sm.get_transitions(run_id)]


def details(orch, run_id):
    return " | ".join((t["details"] or "") for t in orch.sm.get_transitions(run_id))


# ------------------------------------------------------------------ 검사기 먼저 — 들어오면 안 되는 것

def test_fabricated_value_never_lands_in_worktree(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    factory, _ = fake_factory(tmp_path, monkeypatch, [FAKE])
    res = stage.run_stage(make_order(), str(wt), tmp_path / "run", factory=factory)
    assert res["counts"]["accepted"] == 0 and res["counts"]["rejected"] == 1
    assert res["files"] == [] and not (wt / CSV_PATH).exists()
    assert (tmp_path / "run" / "collect" / "rejected.jsonl").exists()      # 증거는 실행 폴더에 남는다


@pytest.mark.parametrize("order_over,code", [
    ({"allowed_paths": ["src/"]}, "COLLECT_PATH_DENIED"),                              # allowed 밖
    ({"protected_paths": ["data/sources/*.csv"]}, "COLLECT_PATH_DENIED"),              # 보호 경로
    ({"allowed_paths": ["**"], "collect": collect_spec(".git/x.csv")}, "COLLECT_PATH_DENIED"),   # 제어면
])
def test_output_path_out_of_scope_is_refused_before_web(tmp_path, monkeypatch, order_over, code):
    wt = tmp_path / "wt"
    wt.mkdir()
    factory, d = fake_factory(tmp_path, monkeypatch, [GOOD])
    with pytest.raises(stage.CollectStop) as exc:
        stage.run_stage(make_order(**order_over), str(wt), tmp_path / "run", factory=factory)
    assert exc.value.code == code
    assert not list((d / "prompts").iterdir())                 # LLM 을 부르기 전에 멈췄다
    assert not list(wt.rglob("*.csv"))


def test_symlinked_parent_escape_is_refused(tmp_path, monkeypatch):
    wt, outside = tmp_path / "wt", tmp_path / "outside"
    wt.mkdir()
    outside.mkdir()
    (wt / "data").symlink_to(outside, target_is_directory=True)
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    with pytest.raises(stage.CollectStop):
        stage.run_stage(make_order(), str(wt), tmp_path / "run", factory=factory)
    assert not list(outside.rglob("*"))


def test_existing_file_at_output_path_is_not_overwritten(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "data" / "sources").mkdir(parents=True)
    (wt / CSV_PATH).write_text("mine\n", encoding="utf-8")
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    with pytest.raises(stage.CollectStop) as exc:
        stage.run_stage(make_order(), str(wt), tmp_path / "run", factory=factory)
    assert exc.value.code == "COLLECT_OUTPUT_EXISTS" and (wt / CSV_PATH).read_text(encoding="utf-8") == "mine\n"


def test_policy_wording_in_query_hint_stops_for_owner(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    spec = collect_spec()
    spec["targets"][0]["query_hint"] = "카드 결제 정보를 입력해 주문"
    factory, d = fake_factory(tmp_path, monkeypatch, [GOOD])
    with pytest.raises(stage.CollectStop) as exc:
        stage.run_stage(make_order(collect=spec), str(wt), tmp_path / "run", factory=factory)
    assert exc.value.code == "COLLECT_ORDER_POLICY" and exc.value.state == State.HUMAN_REQUIRED
    assert not list((d / "prompts").iterdir())


def test_default_deny_survives_head_supplied_domains(tmp_path):
    data = stage.collect_order_data(make_order(collect=collect_spec(domains={"allow": [], "deny": []})))
    assert "place.naver.com" in data["domains"]["deny"] and data["providers"]["report_health_db"] is True
    assert data["requested_at_kst"].endswith("+09:00") and data["order_id"] == "collect"


def test_purpose_is_cleaned_from_multiline_goal():
    data = stage.collect_order_data(make_order(goal="첫 줄\n둘째 줄\t" + "가" * 900))
    assert "\n" not in data["purpose"] and len(data["purpose"]) <= 500


# ------------------------------------------------------------------ 상태 전이 · ORDER 검사

def test_collecting_transitions():
    sm = StateMachine()
    assert sm.can_transition(State.ORDER_READY, State.COLLECTING)
    assert sm.can_transition(State.COLLECTING, State.BUILDING)
    assert sm.can_transition(State.ORDER_READY, State.BUILDING)             # collect 가 없으면 그대로
    assert not sm.can_transition(State.COLLECTING, State.REVIEWING)
    assert not sm.can_transition(State.COLLECTING, State.VERIFYING)
    assert sm.can_transition(State.COLLECTING, State.HUMAN_REQUIRED)


def test_order_conflicts_cover_collect():
    assert autopilot.order_conflicts(make_order()) == []
    assert any("allowed_paths" in p for p in autopilot.order_conflicts(make_order(allowed_paths=["src/"])))
    assert any("protected" in p for p in autopilot.order_conflicts(make_order(protected_paths=["data/"])))
    bad = collect_spec()
    bad["targets"][0]["fields"] = ["phone"]
    assert any("collect" in p for p in autopilot.order_conflicts(make_order(collect=bad)))
    assert any("collect" in p for p in autopilot.order_conflicts(make_order(collect=collect_spec("../x.csv"))))


def test_order_text_includes_collect_hints_for_policy_scan():
    spec = collect_spec()
    spec["targets"][0]["query_hint"] = "이태원 스테이크 주소"
    assert "이태원 스테이크 주소" in autopilot.order_text(make_order(collect=spec))


def test_order_without_collect_is_unchanged():
    o = OrderSchema(goal="g")
    assert o.collect is None and stage.unchanged_paths(None, "/nonexistent") == set()


# ------------------------------------------------------------------ 끝에서 끝 — Director 실행

def test_run_collects_before_builder_and_reviewer_sees_rows(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    orch = mock_orch(factory)
    seen, reviewed = [], {}
    real_review = orch.reviewer.review

    def spy(order, git_diff, verification, *a, **kw):
        reviewed.update(diff=git_diff, verification=verification)
        return real_review(order, git_diff, verification, *a, **kw)
    orch.reviewer.review = spy
    # max_files_changed=1 — 수집 CSV 가 Builder 변경으로 세어지면 범위 위반으로 멈춘다(proxy 꺼짐)
    summary = orch.run(proj, "g", builder_fn=adding(seen), order=make_order(max_files_changed=1))
    rid = summary["run_id"]
    assert summary["final_state"] == State.PASS, details(orch, rid)
    assert states(orch, rid) == ["IDLE", State.PLANNING, State.ORDER_READY, State.COLLECTING, State.BUILDING,
                                 State.VERIFYING, State.REVIEWING, State.DECIDING, State.PASS]
    assert seen and seen[0] and "압구정로 108" in seen[0]               # Builder 가 돌기 전에 놓였다
    wt = orch.sm.get_run(rid)["worktree_path"]
    rows = list(csv.DictReader((Path(wt) / CSV_PATH).open(encoding="utf-8")))
    assert rows[0]["name"] == "구스테이크528" and rows[0]["lat"] == ""
    assert "압구정로 108" in reviewed["diff"] and CSV_PATH in reviewed["diff"]      # 리뷰어 diff 에 행이 보인다
    col = reviewed["verification"]["collection"]
    assert col["accepted"] == 1 and col["rows"] == 1 and col["files"] == [CSV_PATH]
    rec = orch._load_json(rid, "collected_files.json")
    assert rec["files"][0]["path"] == CSV_PATH and len(rec["files"][0]["sha256"]) == 64
    assert (orch._get_run_dir(rid) / "collect" / "evidence" / "fetch_log.jsonl").exists()
    assert not Path(proj, CSV_PATH).exists()                             # 원본 프로젝트는 그대로


def test_collection_evidence_carries_numbers_only(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [FAKE])
    orch = mock_orch(factory)
    summary = orch.run(proj, "g", builder_fn=adding(), order=make_order())
    rid = summary["run_id"]
    col = (orch._load_json(rid, "verification.json") or {}).get("collection")
    text = json.dumps(col, ensure_ascii=False)
    assert col["rejected"] == 1 and col["reason_codes"] == {"VALUE_NOT_ON_PAGE": 1}
    assert "섬진강대로" not in text and "quote" not in text and "detail" not in text       # 거부된 주장 본문은 싣지 않는다


def test_zero_accepted_is_not_human_required(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [FAKE])
    orch = mock_orch(factory)
    seen = []
    summary = orch.run(proj, "g", builder_fn=adding(seen), order=make_order())
    rid = summary["run_id"]
    assert summary["final_state"] == State.PASS, details(orch, rid)
    assert seen == [None]                                               # 빈 CSV 를 놓지 않는다 — 머리글만 있는 파일은 산출물 흉내다
    assert State.COLLECTING in states(orch, rid) and State.HUMAN_REQUIRED not in states(orch, rid)
    assert orch._load_json(rid, "collected_files.json")["files"] == []


def test_builder_edit_of_collected_file_counts_as_builder_change(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    orch = mock_orch(factory)

    def tamper(b, o, w):
        p = Path(w) / CSV_PATH
        p.write_text(p.read_text(encoding="utf-8") + "지어낸집,서울 어딘가 1,,,,,,,,,\n", encoding="utf-8")
        (Path(w) / "tests" / "test_y.py").write_text("def test_3(): pass\n", encoding="utf-8")
        return ["tests/test_y.py"]
    summary = orch.run(proj, "g", builder_fn=tamper, order=make_order(max_files_changed=1))
    # 4차 리뷰 D — 범위 위반(사람이 풀어 줄 수 있다)이 아니라 COLLECTED_FILE_MODIFIED 로 닫힌 쪽에서 멈춘다.
    # max_files_changed 를 넉넉히 주면 예전에는 그대로 리뷰까지 갔다(tests/test_collector_review4.py 가 그 경우를 본다)
    assert summary["final_state"] == State.FAIL
    assert "COLLECTED_FILE_MODIFIED" in details(orch, summary["run_id"])


def test_provider_failure_stops_before_builder(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD], exit_code=1, stderr="usage limit reached")
    orch = mock_orch(factory)
    calls = []
    summary = orch.run(proj, "g", builder_fn=lambda b, o, w: calls.append(w) or [], order=make_order())
    rid = summary["run_id"]
    assert summary["final_state"] == State.HUMAN_REQUIRED and calls == []
    assert "PROVIDER_FAILURE" in details(orch, rid)
    assert autopilot.USER_STOP_MARKERS and any(m in details(orch, rid) for m in autopilot.USER_STOP_MARKERS)
    wt = orch.sm.get_run(rid)["worktree_path"]
    assert not (Path(wt) / CSV_PATH).exists()
    st = orch.approval_state(rid)                                       # 승인으로 Builder 를 건너뛰어 다시 열 수 없다
    assert st["stage"] != "POST_BUILD" and not st.get("approvable")


def test_invalid_collect_spec_fails_run_without_builder(proj, tmp_path, monkeypatch):
    factory, d = fake_factory(tmp_path, monkeypatch, [GOOD])
    orch = mock_orch(factory)
    bad = collect_spec()
    bad["targets"][0]["known"] = {}                                     # known.name 필수
    calls = []
    summary = orch.run(proj, "g", builder_fn=lambda b, o, w: calls.append(w) or [], order=make_order(collect=bad))
    assert summary["final_state"] == State.FAIL and calls == []
    assert "COLLECT_ORDER_REJECTED" in details(orch, summary["run_id"])
    assert not list((d / "prompts").iterdir())


def test_no_collect_means_no_collecting_state(proj):
    orch = mock_orch()
    summary = orch.run(proj, "g", builder_fn=adding(), order=OrderSchema(goal="g", allowed_paths=["tests/", "src/"]))
    rid = summary["run_id"]
    assert summary["final_state"] == State.PASS and State.COLLECTING not in states(orch, rid)
    assert orch._load_json(rid, "collected_files.json") is None


def _stuck(proj, target, order):
    sm = StateMachine()
    run_id = f"RUN-COLLECT-{target}"
    sm.create_run(run_id, str(proj), "g")
    wt, br = GitManager.create_worktree(str(proj), run_id)
    sm.update_run_worktree(run_id, wt, br)
    for st in (State.PLANNING, State.ORDER_READY, State.COLLECTING):
        sm.transition_to(run_id, st)
        if st == target:
            break
    return run_id, wt


def test_resume_from_collecting_recollects_idempotently(proj, tmp_path, monkeypatch):
    order = make_order()
    run_id, wt = _stuck(proj, State.COLLECTING, order)
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD, GOOD])
    orch = mock_orch(factory)
    orch._save_json(run_id, "order.json", order)
    orch._save_json(run_id, "baseline_verification.json",
                    {"passed": True, "summary": "Test: PASS", "test_counts": {"passed": 2, "failed": 0, "total": 2}})
    # 끊기기 전 수집이 놓고 간 파일 — 다시 수집할 때 지우고 새로 놓는다
    first = stage.run_stage(order, wt, orch._get_run_dir(run_id), factory=factory)
    orch._save_json(run_id, "collected_files.json", first["record"])
    seen = []
    summary = orch.resume(run_id, builder_fn=adding(seen))
    assert summary["final_state"] == State.PASS, details(orch, run_id)
    assert seen and "압구정로 108" in seen[0]
    assert "BASELINE_MEASURED" not in [a["action"] for a in orch.sm.get_audit_logs(run_id)]    # 기준선을 다시 재지 않는다
    assert states(orch, run_id).count(State.COLLECTING) == 1


def test_pristine_check_ignores_collected_files(proj, tmp_path, monkeypatch):
    run_id, wt = _stuck(proj, State.ORDER_READY, make_order())
    orch = mock_orch()
    (Path(wt) / "data" / "sources").mkdir(parents=True)
    (Path(wt) / CSV_PATH).write_text("h\n", encoding="utf-8")
    assert not orch._worktree_is_pristine(wt)                           # 기본 동작은 그대로
    assert orch._worktree_is_pristine(wt, ignore={CSV_PATH})
    (Path(wt) / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert not orch._worktree_is_pristine(wt, ignore={CSV_PATH})


# ------------------------------------------------------------------ 세 역할이 보는 것

def test_reviewer_prompts_print_collection_counts(tmp_path, monkeypatch):
    from agents import claude_reviewer, codex_reviewer
    ver = {"passed": True, "summary": "ok", "test_counts": {"passed": 1},
           "collection": {"status": "OK", "accepted": 2, "rejected": 1, "unverifiable": 3, "rows": 2,
                          "reason_codes": {"VALUE_NOT_ON_PAGE": 1}, "files": [CSV_PATH]}}
    for mod, cls, prov in ((codex_reviewer, codex_reviewer.CodexReviewer, "openai"),
                           (claude_reviewer, claude_reviewer.ClaudeReviewer, "anthropic")):
        captured = {}

        class Boom(Exception):
            pass

        def fake_run(*a, **kw):
            captured["prompt"] = kw.get("stdin_data") or ""
            raise Boom()
        monkeypatch.setattr(mod.ProcessTreeRunner, "run", staticmethod(fake_run))
        exe = tmp_path / f"fake-{prov}"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        rev = cls(provider=prov, executable=str(exe))
        try:
            rev._review_with_cli(OrderSchema(goal="g"), "diff", ver, str(tmp_path))
        except Boom:
            pass
        assert "WEB COLLECTION" in captured["prompt"] and '"accepted": 2' in captured["prompt"], mod.__name__
        captured.clear()
        try:
            rev._review_with_cli(OrderSchema(goal="g"), "diff", {"passed": True}, str(tmp_path))
        except Boom:
            pass
        assert "WEB COLLECTION" not in captured["prompt"]               # 수집이 없으면 프롬프트는 그대로다


def test_proposal_builder_prompt_includes_collected_csv(tmp_path, monkeypatch):
    from agents import proposal_builder
    wt = tmp_path / "wt"
    (wt / "data" / "sources").mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (wt / CSV_PATH).write_text("name,address\n구스테이크528,서울특별시 강남구 압구정로 108\n", encoding="utf-8")
    (wt / "data" / "other.csv").write_text("비밀스러운 다른 자료\n", encoding="utf-8")
    captured = {}

    def fake_invoke(self, domain, prompt, cwd, stage_name):
        captured["prompt"] = prompt
        return {"output": '{"files":[],"edits":[],"summary":"x"}', "invocation": "x", "exit_code": 0,
                "duration_sec": 0.0}
    monkeypatch.setattr(proposal_builder.TriadResearchRunner, "invoke", fake_invoke)
    monkeypatch.setattr(proposal_builder, "prepare_project", lambda tmp: None)
    exe = tmp_path / "fake-claude"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    b = proposal_builder.ProposalBuilder(provider="claude", executable=str(exe))
    with pytest.raises(ValueError):
        b.run(make_order(), str(wt))
    assert "압구정로 108" in captured["prompt"] and CSV_PATH in b.last_context_files
    assert "비밀스러운 다른 자료" not in captured["prompt"]              # data/ 의 다른 파일은 여전히 싣지 않는다


def test_head_is_told_about_collect():
    from agents.head import BaseHead

    class H(BaseHead):
        provider = "mock"

        def create_order(self, goal, project_context):
            raise NotImplementedError

        def decide(self, *a, **kw):
            raise NotImplementedError

        def chat(self, *a, **kw):
            raise NotImplementedError

        def get_provenance(self, exit_code=0):
            raise NotImplementedError

        def _invoke_cli(self, prompt, cwd=None):
            self.prompt = prompt
            return "{}"

        def _extract_json(self, raw):
            return {}
    h = H.__new__(H)
    h.plan_next("goal", [], {})
    assert '"collect"' in h.prompt and "cannot read the web" in h.prompt and "ENV_NO_WEB" in h.prompt
    assert '"collect"' in BaseHead.ORDER_SCHEMA_TEXT


def test_mission_history_gets_collection_counts(proj, tmp_path, monkeypatch):
    from core.mission import MissionRunner
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    orch = mock_orch(factory)
    summary = orch.run(proj, "g", builder_fn=adding(), order=make_order())
    ev = MissionRunner._evidence(orch, summary["run_id"])
    assert ev["collection"] == {"accepted": 1, "rejected": 0, "unverifiable": 0, "rows": 1, "status": "OK"}
    plain = mock_orch().run(proj, "g", builder_fn=adding(),
                            order=OrderSchema(goal="g", allowed_paths=["tests/", "src/"]))
    assert MissionRunner._evidence(orch, plain["run_id"])["collection"] is None


def test_dashboard_knows_collecting():
    import dashboard_i18n
    import dashboard_server
    assert "COLLECTING" in dashboard_server.ACTIVE_STATES and "COLLECTING" in dashboard_server.STAGE_KO
    assert "COLLECTING" in dashboard_i18n.STATE_TEXT


# ------------------------------------------------------------------ 미션이 실제로 지나는 길(HEAD 프록시 켜짐)

def test_diff_line_cap_excludes_collected_rows(proj, tmp_path, monkeypatch):
    factory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])
    orch = mock_orch(factory)
    # Builder 변경은 1줄, 수집 CSV 는 2줄 — 수집 행이 세어지면 max_diff_lines=1 에 걸린다
    summary = orch.run(proj, "g", builder_fn=adding(), order=make_order(max_diff_lines=1))
    assert summary["final_state"] == State.PASS, details(orch, summary["run_id"])


def test_mission_step_with_collect_survives_planning_and_proxy_pipeline(tmp_path, monkeypatch):
    from core.mission import MissionRunner, MissionStore
    from tests.test_v306_head_proxy_autonomy import ORDER, STEP2, mission_factory
    builds, histories = [], []
    step2 = dict(STEP2, allowed_paths=["feature2.py", "tests/**", "data/**"], collect=collect_spec())
    plans = [{"status": "CONTINUE", "summary": "double done", "reason": "next", "order": step2},
             {"status": "DONE", "summary": "ok", "reason": "goal met", "order": None}]
    inner = mission_factory(monkeypatch, plans, builds)
    cfactory, _ = fake_factory(tmp_path, monkeypatch, [GOOD])

    def plan_next(self, goal, history, ctx, cwd=None):
        histories.append(json.loads(json.dumps(history, default=str)))
        return plans.pop(0)
    monkeypatch.setattr(MockHead, "plan_next", plan_next)

    def factory():
        orch = inner()
        orch._collector_factory = cfactory
        return orch
    runner = MissionRunner(factory, MissionStore(tmp_path / "missions"))
    m = runner.create(str(tmp_path / "proj"), "double then quad", OrderSchema(**ORDER),
                      config={"autonomy": {"max_steps": 4}})
    done = runner.run(m["mission_id"])
    assert done["status"] == "DONE", done
    assert done["steps"][1]["order"]["collect"]["output"]["path"] == CSV_PATH      # 계획 → 진입 관문을 지나도 남는다
    res = done["steps"][1]["result"]
    assert res["final_state"] == State.PASS and res["collection"]["accepted"] == 1
    assert "압구정로 108" in (Path(res["worktree_path"]) / CSV_PATH).read_text(encoding="utf-8")
    orch = factory()
    assert State.COLLECTING in states(orch, done["steps"][1]["run_id"])
    assert State.COLLECTING not in states(orch, done["steps"][0]["run_id"])
    assert histories[-1][1]["result"]["collection"]["accepted"] == 1               # 다음 계획에서 HEAD 가 숫자를 본다
    assert orch.approval_state(done["steps"][1]["run_id"])["stage"] is None
