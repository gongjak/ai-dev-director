import os
import shutil
from pathlib import Path
from typing import Dict, Optional

FORBIDDEN_API_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY"
]

def get_sanitized_env() -> Dict[str, str]:
    """
    Builds a sanitized environment dictionary with all pay-per-use API keys stripped.
    Ensures that official CLI tools operate strictly using user subscription login sessions.
    """
    env = os.environ.copy()
    for k in FORBIDDEN_API_KEYS:
        env.pop(k, None)
    # macOS 실측(2026-11-08): 사용자 claude 에 oh-my-claudecode 훅이 깔려 있으면 작업 폴더에 .omc/ 상태 파일을 쓴다.
    # 격리 작업본 · 조사 폴더를 더럽혀 RESEARCH_READ_ONLY_VIOLATION 과 자동 적용 충돌을 낸다. 하위 CLI 에서는 끈다.
    # DISABLE_OMC 만으로는 상태 파일이 남는다(실측) — OMC_STATE_DIR 로 상태를 Director data/ 밖 폴더로 옮긴다.
    env.setdefault("DISABLE_OMC", "1")
    env.setdefault("OMC_STATE_DIR", str(Path(__file__).resolve().parent.parent / "data" / "cli-omc-state"))
    return env

def resolve_cli(name: str) -> Optional[str]:
    """
    Discovers the official CLI executable from OS PATH or standard install locations.
    Returns the resolved absolute path or None.
    """
    # Desktop CLIs live in versioned directories that change after app updates.
    # Prefer native binaries over stale npm wrappers, without changing PATH globally.
    if os.name == 'nt' and os.environ.get('DIRECTOR_TESTING') != '1':
        local = Path(os.environ.get('LOCALAPPDATA', ''))
        roaming = Path(os.environ.get('APPDATA', ''))
        root = {'codex': local / 'OpenAI/Codex/bin',
                'claude': roaming / 'Claude/claude-code'}.get(name)
        if root:
            try:
                candidates = sorted(root.glob('*/' + name + '.exe'),
                                    key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    return str(candidates[0].resolve())
            except OSError:
                pass
    try:
        p = shutil.which(name)
    except (PermissionError, OSError):
        p = None
    if p:
        try:
            return str(Path(p).resolve())
        except (PermissionError, OSError):
            return str(p)

    # 환경변수가 비어 있으면(macOS/Linux) Path("") 가 상대경로 "npm/claude" 를 만들어
    # 현재 작업 폴더의 파일을 CLI 로 집을 수 있다 — 그런 후보는 아예 만들지 않는다
    _lad = os.environ.get("LOCALAPPDATA", "")
    _ad = os.environ.get("APPDATA", "")
    local_app_data = Path(_lad) if _lad else None
    app_data = Path(_ad) if _ad else None
    home = Path.home()
    # macOS/Linux (unverified): native installer -> ~/.local/bin, Homebrew -> /opt/homebrew/bin, npm -g -> /usr/local/bin
    posix_dirs = [home / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]

    known = {
        "claude": [
            *([app_data / "npm" / "claude.cmd", app_data / "npm" / "claude"] if app_data else []),
            *[d / "claude" for d in posix_dirs],
            home / ".claude" / "local" / "claude",
        ],
        "codex": [
            *([app_data / "npm" / "codex.cmd", app_data / "npm" / "codex"] if app_data else []),
            *[d / "codex" for d in posix_dirs],
        ],
        "agy": [
            *([local_app_data / "agy" / "bin" / "agy.exe", local_app_data / "agy" / "bin" / "agy.cmd"]
              if local_app_data else []),
            *[d / "agy" for d in posix_dirs],
            home / ".agy" / "bin" / "agy",
        ]
    }

    for cand in known.get(name, []):
        # A candidate that cannot even be stat'ed (WinError 5 / PermissionError / OSError) is skipped;
        # the next candidate is tried and, if none works, None (= CLI unavailable) is returned.
        try:
            present = cand.exists()
        except (PermissionError, OSError):
            continue
        if not present:
            continue
        try:
            bin_dir = str(cand.parent)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            try:
                return str(cand.resolve())
            except (PermissionError, OSError):
                return str(cand)
        except (PermissionError, OSError):
            continue

    return None


def run_capture(argv, cwd=None, timeout=None, env=None):
    """
    subprocess.run wrapper for short non-AI commands (git, --version probes).
    Always shell=False, captured output decoded as UTF-8 with replacement so Korean/Unicode
    paths on Windows (cp949 default codec) never raise inside the reader thread.
    """
    import subprocess
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
        env=env,
    )


def probe_version(executable, timeout: float = 5.0, arg: str = "--version") -> str:
    """
    Runtime-availability probe (NOT an inference call). Runs `<executable> --version` through
    ProcessTreeRunner: shell=False, hard timeout, stdout/stderr drained, whole process tree killed on
    timeout (Windows taskkill /T). Returns the first stdout line or "UNKNOWN".
    """
    from core.process_runner import ProcessTreeRunner
    if not executable:
        return "UNKNOWN"
    try:
        res = ProcessTreeRunner.run(
            argv=[str(executable), arg],
            env=get_sanitized_env(),
            timeout=float(timeout),
            no_output_timeout=None,
            heartbeat_interval=max(float(timeout), 1.0),
        )
    except Exception:
        return "UNKNOWN"
    if res.timed_out or res.exit_code != 0:
        return "UNKNOWN"
    first = (res.stdout or "").strip().split("\n")[0].strip()
    return first or "UNKNOWN"


# Claude 역할은 opus 로만 돈다 (포크 정책). 계정 기본 모델에 맡기지 않고 명령 줄에 적는다.
CLAUDE_MODEL_DEFAULT = "opus"


def claude_model_args() -> list:
    """claude CLI 에 붙일 --model 인자. DIRECTOR_CLAUDE_MODEL 로 바꿀 수 있으나 sonnet 은 거부한다."""
    model = (os.environ.get("DIRECTOR_CLAUDE_MODEL") or CLAUDE_MODEL_DEFAULT).strip()
    if "sonnet" in model.lower():
        raise RuntimeError("CONFIG_ERROR: Sonnet 모델은 금지되어 있다 (DIRECTOR_CLAUDE_MODEL=" + model + ")")
    return ["--model", model]


# Collector(웹 자료 수집)만 haiku 로 돈다 — 공작 결정 2026-11-09. 받은 본문에서 값을 뽑기만 하므로 opus 가 필요 없다.
# claude_model_args() 의 「opus 만」 정책은 그대로이고, 이 예외는 core/collector 에서만 쓴다.
COLLECTOR_MODEL_DEFAULT = "haiku"


def collector_model_args() -> list:
    """Collector 용 --model 인자. DIRECTOR_COLLECTOR_MODEL 로 바꿀 수 있으나 sonnet 은 거부한다."""
    model = (os.environ.get("DIRECTOR_COLLECTOR_MODEL") or COLLECTOR_MODEL_DEFAULT).strip()
    if "sonnet" in model.lower():
        raise RuntimeError("CONFIG_ERROR: Sonnet 모델은 금지되어 있다 (DIRECTOR_COLLECTOR_MODEL=" + model + ")")
    return ["--model", model]
