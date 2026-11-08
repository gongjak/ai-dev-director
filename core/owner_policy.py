"""공작(소유자) 위임 정책 — HEAD 가 대신 정하지 못하는 범주를 코드로 고정한다.

HEAD 대리 결정(core/autopilot.py)은 대부분을 스스로 정하고 기록한다. 아래 범주만은 HEAD 의 판정과
무관하게 소유자에게 올린다. HEAD 가 APPROVE 를 내도 뒤집히지 않는다 (결정론적 검사).

  소유자 확인 범주 (OWNER_ONLY)
    1. 결제 · 유료 서비스
    2. 실계좌 주문
    3. 진짜 자격 증명 · 계정 생성
    4. 되돌릴 수 없는 외부 행동 (운영 배포 · 외부 메시지 발송 · force push 등)
    5. NAS 자료 삭제
    6. 고객사(ibtravel 등) 운영 서버 쓰기

  HEAD 범위 밖 (OUT_OF_SCOPE) — 처음 범위는 저장소 개발 작업뿐이다
    - 노드 배포: kpaean · NAS · 청비미니 로 ssh/scp/rsync 하는 일
"""
import re
from typing import Dict, Iterable, List, Optional

OWNER_PREFIX = "Owner-only action"          # Scope Guard 문자열 접두. autopilot.USER_STOP_MARKERS 에 들어간다
OUT_OF_SCOPE_PREFIX = "Out of HEAD scope"

# 노드 이름 — 청비 노드 인벤토리의 SSH 별칭 · 호스트 이름
NODE_NAMES = ("kpaean", "cheongbimini", "cheongbi-mini", "청비미니", "nas", "nas2", "synology")
CLIENT_PROD_NAMES = ("ibtravel", "이번여행")

_NODE_ALT = "|".join(re.escape(n) for n in NODE_NAMES)
_CLIENT_ALT = "|".join(re.escape(n) for n in CLIENT_PROD_NAMES)

# (범주, 정규식) — 글(ORDER · 판단 질문)에 쓴다. 대소문자 무시
OWNER_TEXT_RULES = (
    ("PAYMENT", re.compile(r"payment|purchase|billing|credit card|paid (?:plan|service|api)|결제|구매|유료|과금", re.I)),
    ("LIVE_ACCOUNT_ORDER", re.compile(r"real[- ]money|live (?:account|trading)|real (?:account|broker)|실계좌|실거래|실자금|실제 자금", re.I)),
    ("REAL_CREDENTIAL", re.compile(r"credential|api key|password|private key|secret token|account creation|create an account|"
                                   r"자격 ?증명|비밀번호|api 키|계정 생성|토큰 발급", re.I)),
    ("IRREVERSIBLE_EXTERNAL", re.compile(r"production deploy|deploy to production|force[- ]push|send (?:an? )?(?:email|message)|"
                                         r"운영 배포|강제 푸시|메일 발송|메시지 발송", re.I)),
    ("NAS_DELETE", re.compile(r"(?:\b(?:delete|remove|rm|purge|wipe)\b|삭제|지우|지운).{0,40}(?:\bnas\d?\b|synology|volume\d|/volume)"
                              r"|(?:\bnas\d?\b|synology|/volume\d?).{0,40}(?:\b(?:delete|remove|rm|purge|wipe)\b|삭제|지우|지운)", re.I)),
    ("CLIENT_PROD_WRITE", re.compile(r"(?:" + _CLIENT_ALT + r").{0,60}(?:운영|prod|production|server|서버|rds|ec2|db)"
                                     r"|(?:운영|prod|production).{0,40}(?:" + _CLIENT_ALT + r")", re.I)),
)

OUT_OF_SCOPE_TEXT_RULES = (
    ("NODE_DEPLOY", re.compile(r"(?:\b(?:ssh|scp|rsync|deploy|배포)\b|배포|설치).{0,40}(?:\b(?:" + _NODE_ALT + r")\b|청비미니)"
                               r"|(?:\b(?:" + _NODE_ALT + r")\b|청비미니).{0,40}(?:\b(?:ssh|scp|rsync|deploy)\b|배포|설치)", re.I)),
)

# 코드(추가된 줄)에 쓴다 — 시험이 코드를 실행하기 전에 검사한다
CODE_RULES = (
    (OUT_OF_SCOPE_PREFIX, "node deployment (ssh/scp/rsync to a node)",
     re.compile(r"\b(?:ssh|scp|rsync)\b[^\n]{0,120}\b(?:" + _NODE_ALT + r")\b", re.I)),
    (OWNER_PREFIX, "client production server access",
     re.compile(r"\b(?:ssh|scp|rsync|psql|mysql|aws)\b[^\n]{0,160}(?:" + _CLIENT_ALT + r")", re.I)),
    (OWNER_PREFIX, "NAS data deletion",
     re.compile(r"(?:\brm\b|shutil\.rmtree|os\.remove|\.unlink\()[^\n]{0,160}(?:/volume\d|\bnas\d?\b|synology)", re.I)),
    (OWNER_PREFIX, "force push",
     re.compile(r"git\s+push\b[^\n]*(?:--force\b|\s-f\b)", re.I)),
)


def _hits(rules, text: str) -> List[str]:
    return [code for code, rx in rules if rx.search(text or "")]


def owner_only_reasons(text: str) -> List[str]:
    """소유자에게 올려야 하는 범주 코드 목록 (예: ['NAS_DELETE'])."""
    return _hits(OWNER_TEXT_RULES, text)


def out_of_scope_reasons(text: str) -> List[str]:
    """HEAD 범위 밖 범주 코드 목록 (예: ['NODE_DEPLOY'])."""
    return _hits(OUT_OF_SCOPE_TEXT_RULES, text)


def hard_stop_reasons(text: str) -> List[str]:
    """HEAD 가 무엇을 말하든 멈추는 사유. 비면 HEAD 가 정한다."""
    return ([f"OWNER_ONLY:{c}" for c in owner_only_reasons(text)]
            + [f"OUT_OF_SCOPE:{c}" for c in out_of_scope_reasons(text)])


def code_findings(raw_git_diff: str, new_files: Optional[Dict[str, str]] = None) -> List[str]:
    """추가된 코드에서 노드 배포 · 고객사 운영 서버 · NAS 삭제 · force push 를 찾는다.
    결과는 Scope Guard 문자열 '<접두>: <경로> (<설명>)'."""
    hits: Dict[str, tuple] = {}

    def scan(path, line):
        for prefix, label, rx in CODE_RULES:
            if rx.search(line):
                hits.setdefault(path, (prefix, label))

    current = None
    for line in (raw_git_diff or "").splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith("+") and current not in (None, "/dev/null"):
            scan(current, line[1:])
    for path, content in (new_files or {}).items():
        for raw in (content or "").splitlines():
            scan(path, raw)
    return [f"{prefix}: {p} ({label})" for p, (prefix, label) in sorted(hits.items())]


def decision_record(reasons: Iterable[str], decided_by: str) -> dict:
    """감사 기록에 남길 한 줄 형식."""
    reasons = list(reasons)
    return {"decided_by": decided_by, "escalate": bool(reasons), "reasons": reasons}
