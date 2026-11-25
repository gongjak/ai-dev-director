"""포크: 실행 산출물 폴더(runs/)를 DIRECTOR_RUNS_DIR 로 옮길 수 있다 — 시험이 저장소 runs/ 를 더럽히지 않게."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_default_runs_dir_is_repo_runs(monkeypatch):
    from core.runs_dir import runs_root
    monkeypatch.delenv("DIRECTOR_RUNS_DIR", raising=False)
    assert runs_root() == REPO / "runs"
    monkeypatch.setenv("DIRECTOR_RUNS_DIR", "   ")
    assert runs_root() == REPO / "runs"


def test_env_overrides_runs_dir_everywhere(monkeypatch, tmp_path):
    from core.orchestrator import Orchestrator
    from core.result_manager import ResultManager
    from core.runs_dir import runs_root
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("DIRECTOR_RUNS_DIR", str(target))
    assert runs_root() == target
    assert ResultManager.run_dir("RUN-X") == target / "RUN-X"
    run_dir = Orchestrator._get_run_dir(None, "RUN-X")
    assert run_dir == target / "RUN-X" and (run_dir / "logs").is_dir()
    assert not (REPO / "runs" / "RUN-X").exists()


def test_suite_runs_dir_is_outside_the_repo():
    from core.runs_dir import runs_root
    assert os.environ.get("DIRECTOR_RUNS_DIR")
    assert REPO not in runs_root().resolve().parents and runs_root().resolve() != REPO / "runs"
