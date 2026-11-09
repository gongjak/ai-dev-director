"""빌더 교체 순서 — agy 를 못 쓰면 claude → codex (2026-11-09 공작 지시)."""
from pathlib import Path

import yaml

from core import autopilot


class _PM:
    def __init__(self, usable):
        self._usable = usable

    def usable_domains(self):
        return list(self._usable)


ORDER = ["claude", "codex"]


def test_agy_down_goes_to_claude_first_even_if_claude_is_avoided():
    pm = _PM(["ANTHROPIC", "OPENAI"])
    # claude 만 다른 역할에 있어도(avoid) 순서는 claude 가 먼저다
    alt = autopilot.pick_alternative(pm, exclude={"GOOGLE"}, avoid={"ANTHROPIC"}, order=ORDER)
    assert alt == "claude"


def test_claude_tried_goes_to_codex():
    pm = _PM(["ANTHROPIC", "OPENAI"])
    alt = autopilot.pick_alternative(pm, exclude={"GOOGLE", "ANTHROPIC"}, avoid=set(), order=ORDER)
    assert alt == "openai"


def test_claude_unusable_goes_to_codex():
    pm = _PM(["OPENAI"])
    assert autopilot.pick_alternative(pm, exclude={"GOOGLE"}, order=ORDER) == "openai"


def test_none_left_returns_none():
    pm = _PM(["GOOGLE"])
    assert autopilot.pick_alternative(pm, exclude={"GOOGLE"}, order=ORDER) is None


def test_without_order_keeps_old_behaviour():
    pm = _PM(["ANTHROPIC", "OPENAI"])
    assert autopilot.pick_alternative(pm, exclude={"GOOGLE"}, avoid={"ANTHROPIC"}) == "openai"


def test_config_sets_builder_order():
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["builder"]["provider"] == "antigravity"
    assert cfg["builder"]["failover_order"] == ["claude", "codex"]


def test_role_assignment_at_start_uses_order_when_agy_down():
    """실행 시작 때 agy 가 이미 한도면 초기 배정도 claude 를 먼저 고른다."""
    import logging
    from core.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.pm = _PM(["ANTHROPIC", "OPENAI"])
    o.config = {"builder": {"mode": "proposal", "failover_order": ["claude", "codex"]}}
    o.sm = type("SM", (), {"log_audit": lambda *a, **k: None})()
    # HEAD · 리뷰가 claude 라 claude 만 avoid 에 든다 — 그래도 claude 여야 한다
    res = o._autonomous_role_assignment("claude", "antigravity", "claude", "RUN-T", logging.getLogger("t"))
    assert res["builder_provider"] == "claude", res
