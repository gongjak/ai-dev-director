# 포크: 대화 경로 ORDER 가 형식에 어긋나면 한 번 다시 묻는다 · 프롬프트에 collect 형식이 실린다
import json
import pytest
from pydantic import ValidationError
from agents.claude_head import ClaudeHead

GOOD = {"type": "DEV_TASK", "message": "ok", "order": {"goal": "g", "allowed_paths": ["src/**"], "protected_paths": [],
        "acceptance_criteria": ["a"], "allowed_deletions": [], "max_files_changed": 5, "max_diff_lines": 100,
        "context": "c", "risk_level": "LOW", "requires_human_approval": False}}
BAD = json.loads(json.dumps(GOOD)); BAD["order"]["collect"] = {"targets": "글", "by": "x"}


def _head(outputs, prompts):
    h = ClaudeHead.__new__(ClaudeHead)
    it = iter(outputs)
    h._invoke_cli = lambda prompt, cwd=None: (prompts.append(prompt), json.dumps(next(it)))[1]
    h._extract_json = lambda raw: json.loads(raw)
    return h


def test_retry_once_then_ok():
    prompts = []
    data = _head([BAD, GOOD], prompts).chat("m", {"project_path": None}, [])
    assert data == GOOD and len(prompts) == 2
    assert '"collect"' in prompts[0] and "FAILED SCHEMA VALIDATION" in prompts[1]


def test_two_bad_raise():
    with pytest.raises(ValidationError):
        _head([BAD, BAD], []).chat("m", {"project_path": None}, [])
