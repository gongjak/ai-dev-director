"""포크 4차 리뷰(agy Neon high · fable) 지적의 재현 시험 — 참조 폴더(A · B · C) · runs 폴더(K).

수집 단계 쪽(D ~ J)은 tests/test_collector_review4.py 에 있다.
"""
import json
import os
from pathlib import Path

import pytest

from core import builder_context
from core.builder_context import referenced_documents
from schemas.order import OrderSchema

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX 경로 표기")


def _names(docs):
    return sorted(Path(k).name for k in docs if not k.endswith("/"))


def _repair_context(test_output, review="ok"):
    """orchestrator 가 수리 라운드에 붙이는 꼴 그대로 — pytest 출력 · 리뷰 글이 order.context 에 들어간다."""
    ro = {"round": 1, "head": {"rationale": "fix"}, "review": {"summary": review}, "test_summary": "1 failed",
          "test_output": test_output[-12000:]}
    return "base" + "\nHEAD REPAIR ORDER: " + json.dumps(ro, ensure_ascii=False)


# ---------------------------------------------------------------- A. 폴더는 goal · reference_paths 에서만 알아본다
def test_a_folders_named_in_context_are_neither_snapshotted_nor_fatal(tmp_path):
    roots = [tmp_path / "roots"]
    inside = tmp_path / "roots" / "private-infra"
    inside.mkdir(parents=True)
    (inside / "nodes.md").write_text("내부 노드 목록", encoding="utf-8")
    outside = tmp_path / "pytest-of-u" / "pytest-3" / "test_a0"      # pytest 가 남기는 tmp_path (roots 밖)
    outside.mkdir(parents=True)
    out = f"___ test_a ___\n\ntmp_path = PosixPath('{outside}')\n\n>       assert (tmp_path / 'o.csv').exists()\n"
    for ctx in (_repair_context(out), _repair_context("1 failed", "the test writes to /tmp which is wrong"),
                _repair_context("1 failed", f"compare with {inside} layout")):
        order = OrderSchema(goal="g", context=ctx, acceptance_criteria=[f"{inside} 와 같은 구조"])
        assert referenced_documents(order, roots, project_roots=[tmp_path / "proj"]) == {}


def test_a_goal_folder_outside_roots_or_symlinked_is_skipped_but_explicit_still_fails(tmp_path):
    roots = [tmp_path / "roots"]
    (tmp_path / "roots").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "roots" / "alias").symlink_to(outside, target_is_directory=True)
    for goal in ("임시 파일은 /tmp 에 두지 않는다", "실행 파일은 /usr/bin 에 있다고 가정하지 않는다",
                 f"{outside} 는 건드리지 않는다", f"{tmp_path / 'roots' / 'alias'} 도 아니다"):
        assert referenced_documents(OrderSchema(goal=goal), roots, project_roots=[tmp_path / "proj"]) == {}, goal
    with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):      # 명시한 참조는 여전히 닫힌 쪽이다
        referenced_documents(OrderSchema(goal="g", reference_paths=[str(outside)]), roots)


def test_a_goal_folder_inside_roots_is_still_snapshotted(tmp_path):
    hub = tmp_path / "roots" / "hub"
    hub.mkdir(parents=True)
    (hub / "README.md").write_text("HUB", encoding="utf-8")
    docs = referenced_documents(OrderSchema(goal=f"{hub} 폴더를 참고한다"), [tmp_path / "roots"])
    assert docs[str(hub / "README.md")] == "HUB"


# ---------------------------------------------------------------- B. Director 자신 · solomon.denied 는 참조 루트가 아니다
@pytest.fixture
def director_layout(tmp_path, monkeypatch):
    base = tmp_path / "experiments"
    director, blocked, ok = base / "director", base / "blocked", base / "ok"
    (director / "runs").mkdir(parents=True)
    (director / "runs" / "evidence.md").write_text("RUN EVIDENCE", encoding="utf-8")
    (director / "settings.md").write_text("DIRECTOR", encoding="utf-8")
    for d, body in ((blocked, "BLOCKED"), (ok, "OK")):
        d.mkdir()
        (d / "a.md").write_text(body, encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(builder_context, "DIRECTOR_ROOT", director)
    monkeypatch.setattr("core.env_utils.root_config", lambda: {"safety": {"reference_roots": [str(base)]},
                                                               "solomon": {"denied": [str(blocked)]}})
    return {"base": base, "director": director, "blocked": blocked, "ok": ok, "wt": wt,
            "roots": builder_context.reference_roots(wt)}


def test_b_director_root_and_denied_folders_are_refused_as_references(director_layout):
    L = director_layout
    for ref in (L["director"] / "runs", L["director"], L["blocked"], L["director"] / "runs" / "evidence.md",
                L["blocked"] / "a.md"):
        with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
            referenced_documents(OrderSchema(goal="g", reference_paths=[str(ref)]), L["roots"], project_roots=[L["wt"]])
    # goal 에 이름만 나온 것은 조용히 건너뛴다
    order = OrderSchema(goal=f"{L['director'] / 'runs'} 와 {L['blocked']} 를 본다")
    assert referenced_documents(order, L["roots"], project_roots=[L["wt"]]) == {}
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(L["ok"])]), L["roots"], project_roots=[L["wt"]])
    assert docs[str(L["ok"] / "a.md")] == "OK"


def test_b_snapshot_of_a_parent_folder_leaves_out_director_and_denied(director_layout):
    L = director_layout
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(L["base"])]), L["roots"],
                                project_roots=[L["wt"]])
    body = "".join(docs.values())
    assert "OK" in body and "RUN EVIDENCE" not in body and "DIRECTOR" not in body and "BLOCKED" not in body


def test_b_project_inside_a_denied_folder_keeps_its_own_documents(director_layout):
    """Director 저장소가 작업 대상일 때 — 프로젝트 자신의 문서는 빌더가 이미 받는 것이다. 막지 않는다."""
    L = director_layout
    wt = L["director"] / "wt"
    (wt / "docs").mkdir(parents=True)
    (wt / "docs" / "SPEC.md").write_text("SPEC", encoding="utf-8")
    roots = builder_context.reference_roots(wt)
    docs = referenced_documents(OrderSchema(goal=f"{wt / 'docs' / 'SPEC.md'} 를 따른다"), roots, project_roots=[wt])
    assert docs == {str(wt / "docs" / "SPEC.md"): "SPEC"}


# ---------------------------------------------------------------- C. 비밀 — 폴더 자신의 경로 · 내용 · 낱개 파일
def test_c_secret_folder_referenced_directly_is_refused(tmp_path):
    roots = [tmp_path / "r"]
    for name in ("secrets", ".secrets", "credentials"):
        d = tmp_path / "r" / "app" / name
        d.mkdir(parents=True)
        (d / "notes.md").write_text("DB PASSWORD IS HUNTER2", encoding="utf-8")
        with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
            referenced_documents(OrderSchema(goal="g", reference_paths=[str(d)]), roots)
        assert referenced_documents(OrderSchema(goal=f"{d} 폴더를 참고한다"), roots) == {}
        # 그 안쪽 폴더를 가리켜도 같다
        (d / "inner").mkdir()
        (d / "inner" / "x.md").write_text("HUNTER2", encoding="utf-8")
        with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
            referenced_documents(OrderSchema(goal="g", reference_paths=[str(d / "inner")]), roots)


def test_c_snapshot_drops_files_whose_content_looks_secret(tmp_path):
    hub = tmp_path / "roots" / "hub"
    hub.mkdir(parents=True)
    (hub / "config.yaml").write_text("api_key: sk-live-AAAA\ndb_password: hunter2\n", encoding="utf-8")
    (hub / "settings.py").write_text("AWS_SECRET_ACCESS_KEY='abcd'\n", encoding="utf-8")
    (hub / "docker-compose.yml").write_text("environment:\n  POSTGRES_PASSWORD: pw\n", encoding="utf-8")
    (hub / "service-account.json").write_text('{"private_key": "-----BEGIN PRIVATE KEY-----"}', encoding="utf-8")
    (hub / "auth.json").write_text('{"refresh": "1//0g"}', encoding="utf-8")
    (hub / "env.txt").write_text("OPENAI_API_KEY=sk-x\n", encoding="utf-8")
    (hub / "pem.md").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n", encoding="utf-8")
    # 넘겨야 하는 것 — 비밀이라는 낱말만 나오는 코드 · 문서는 그대로 간다
    (hub / "auth.py").write_text("def login(password: str, token=None):\n    return check(password)\n", encoding="utf-8")
    (hub / "README.md").write_text("비밀번호는 환경변수로 넘긴다. password 를 코드에 적지 않는다.\n", encoding="utf-8")
    (hub / "package.json").write_text('{"name": "hub", "author": "x"}', encoding="utf-8")
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), [tmp_path / "roots"])
    assert _names(docs) == ["README.md", "auth.py", "package.json"]
    assert "hunter2" not in "".join(docs.values()) and "sk-" not in "".join(docs.values())
    assert "비밀" in docs[str(hub) + "/"] and "7" in docs[str(hub) + "/"]


def test_c_single_file_reference_with_secret_name_is_refused(tmp_path):
    roots = [tmp_path / "r"]
    (tmp_path / "r" / ".secrets").mkdir(parents=True)
    (tmp_path / "r" / "docs").mkdir()
    for rel in ("docs/api_token.md", "docs/passwords.txt", ".secrets/notes.md"):
        p = tmp_path / "r" / rel
        p.write_text("HUNTER2", encoding="utf-8")
        with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
            referenced_documents(OrderSchema(goal="g", reference_paths=[str(p)]), roots)
        with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
            referenced_documents(OrderSchema(goal=f"{p} 를 읽는다"), roots)
    ok = tmp_path / "r" / "docs" / "SPEC.md"
    ok.write_text("SPEC", encoding="utf-8")
    assert referenced_documents(OrderSchema(goal="g", reference_paths=[str(ok)]), roots) == {str(ok): "SPEC"}


def test_c_test_folder_name_above_the_root_does_not_hide_everything(tmp_path):
    """허용 루트 위쪽 경로의 낱말(pytest 임시 폴더의 `test_secret_…`)은 보지 않는다 — 루트 아래 구성요소만 본다."""
    hub = tmp_path / "secret-parent" / "roots" / "hub"
    hub.mkdir(parents=True)
    (hub / "a.md").write_text("A", encoding="utf-8")
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), [tmp_path / "secret-parent" / "roots"])
    assert _names(docs) == ["a.md"]


# ---------------------------------------------------------------- K. DIRECTOR_RUNS_DIR 상대 경로
def test_k_relative_runs_dir_becomes_absolute_and_stable(monkeypatch, tmp_path):
    from core.runs_dir import runs_root
    monkeypatch.setenv("DIRECTOR_RUNS_DIR", "runs_rel")
    first = runs_root()
    monkeypatch.chdir(tmp_path)                         # 작업 폴더가 바뀌어도(worktree 로 들어가도) 같은 자리다
    assert first.is_absolute() and runs_root() == first and first.name == "runs_rel"
