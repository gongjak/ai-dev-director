"""실행 산출물 폴더(runs/)의 위치 — 한 곳에서만 정한다.

기본은 디렉터 저장소의 `runs/` 다. 환경변수 `DIRECTOR_RUNS_DIR` 를 주면 그 폴더를 쓴다(포크 2026-11-25). 상대 경로는 저장소 루트 기준이다.
시험이 저장소 `runs/` 에 폴더를 쌓던 것을 막으려고 만들었다 — `tests/conftest.py` 가 임시 폴더로 돌린다.
"""
import os
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "runs"


def runs_root() -> Path:
    """부를 때마다 환경변수를 읽는다 — import 시점에 굳히면 시험이 뒤에 바꾼 값을 못 본다."""
    override = (os.environ.get("DIRECTOR_RUNS_DIR") or "").strip()
    if not override:
        return _DEFAULT
    target = Path(os.path.expanduser(override))
    # 상대 경로는 저장소 루트에 붙여 절대 경로로 만든다(4차 리뷰 K) — 그대로 두면 부르는 쪽의 작업 폴더(worktree 등)에
    # 따라 자리가 달라진다. 절대 경로는 건드리지 않는다(macOS 의 /var → /private/var 로 바뀌면 비교가 어긋난다)
    return target if target.is_absolute() else (_DEFAULT.parent / target).resolve()
