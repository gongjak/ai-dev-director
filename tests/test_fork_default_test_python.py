# 포크: PATH 에 python 이 없으면 기본 검증 명령을 Director 인터프리터로 바꾼다
import sys
from core import bootstrap


def _cfg():
    return {'verification': {'test_command': 'python -m pytest tests/'}}


def test_no_python_on_path_uses_director_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda name: None)
    out = bootstrap.configure_runtime(_cfg(), str(tmp_path))
    assert out['verification']['test_command'] == [sys.executable, '-m', 'pytest', 'tests/']


def test_python_on_path_keeps_default(tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/python')
    out = bootstrap.configure_runtime(_cfg(), str(tmp_path))
    assert out['verification']['test_command'] == 'python -m pytest tests/'
