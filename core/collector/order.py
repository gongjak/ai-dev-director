"""수집 지시서(collect_order.json) 스키마 · 기본값 · 한도 · 도메인 정책 (설계 §3)."""
from urllib.parse import unquote
import json
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.owner_policy import hard_stop_reasons

FIELDS = ("address", "official_url", "status")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# 브라우저가 아닌 UA 에 403 을 주는 사이트가 있다(siksinhot 실측 2026-11-09). 공작 승인 아래 저부하 조건과 함께만 쓴다.
DEFAULT_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
DEFAULT_DENY = ["guide.michelin.com", "bluer.co.kr", "map.naver.com", "place.naver.com",
                "map.kakao.com", "place.map.kakao.com"]


class OrderError(ValueError):
    """지시서 거부 — 종료값 1."""


def _clean(text: str, limit: int = 200) -> str:
    if len(text) > limit:
        raise ValueError(f"{limit}자를 넘는다")
    if _CTRL.search(text):
        raise ValueError("제어문자가 있다")
    return text


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    known: Dict[str, str] = Field(default_factory=dict, validate_default=True)
    fields: List[Literal["address", "official_url", "status"]] = Field(min_length=1)
    query_hint: Optional[str] = None
    urls: List[str] = Field(default_factory=list)

    @field_validator("known")
    @classmethod
    def _known(cls, v):
        out = {_clean(k, 40): _clean(val) for k, val in v.items()}
        if not (out.get("name") or "").strip():
            raise ValueError("known.name 이 비었다 — 이름 근접 대조(§6.2)에 쓴다")
        return out

    @field_validator("query_hint")
    @classmethod
    def _hint(cls, v):
        return None if v is None else _clean(v)

    @field_validator("urls")
    @classmethod
    def _urls(cls, v):
        return [_clean(u, 2048) for u in v]

    def hint(self) -> str:
        if self.query_hint:
            return self.query_hint
        k = self.known
        return " ".join(x for x in (k.get("sido"), k.get("area"), k.get("name")) if x)


class Limits(_Strict):
    max_targets: int = Field(40, ge=1, le=200)
    max_llm_calls: int = Field(120, ge=1, le=1000)
    max_urls_per_target: int = Field(3, ge=1, le=5)
    max_fetch_requests: int = Field(200, ge=1, le=1000)
    max_bytes_per_page: int = Field(2 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    fetch_timeout_sec: float = Field(20, gt=0, le=60)
    min_interval_sec_per_host: float = Field(3, ge=1)
    llm_timeout_sec: float = Field(300, gt=0, le=1800)
    max_page_chars: int = Field(60000, ge=1000, le=200000)
    max_total_bytes: int = Field(128 * 1024 * 1024, ge=1024 * 1024, le=1024 * 1024 * 1024)   # 실행 전체 받은 바이트
    user_agent: str = DEFAULT_USER_AGENT

    @field_validator("user_agent")
    @classmethod
    def _ua(cls, v):
        return _clean(v, 300)


class Domains(_Strict):
    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=lambda: list(DEFAULT_DENY))


class Output(_Strict):
    schema_name: Literal["steak-sources-v1", "generic-claims-v1"] = Field(alias="schema")
    path: str

    @field_validator("path")
    @classmethod
    def _path(cls, v):
        p = v.replace("\\", "/")
        if p.startswith("/") or ".." in p.split("/") or not p.endswith(".csv") or re.match(r"^[A-Za-z]:", p):
            raise ValueError("상대 경로 · `..` 없음 · .csv 여야 한다")
        return p


class Providers(_Strict):
    primary: Literal["claude-haiku"] = "claude-haiku"
    report_health_db: bool = False


class CollectOrder(_Strict):
    version: Literal[1]
    # 첫 글자 점 금지 — 「.」 「..」 가 out 폴더의 부모를 가리키지 못하게
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$")
    purpose: str
    targets: List[Target] = Field(min_length=1)
    output: Output
    limits: Limits = Field(default_factory=Limits)
    domains: Domains = Field(default_factory=Domains)
    providers: Providers = Field(default_factory=Providers)
    requested_at_kst: str

    @field_validator("purpose")
    @classmethod
    def _purpose(cls, v):
        return _clean(v, 500)

    @field_validator("requested_at_kst")
    @classmethod
    def _kst(cls, v):
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?\+09:00$", v):
            raise ValueError("+09:00 오프셋이 붙은 ISO 시각이어야 한다")
        return v

    @model_validator(mode="after")
    def _check(self):
        if len(self.targets) > self.limits.max_targets:
            raise ValueError(f"대상 {len(self.targets)}개가 max_targets {self.limits.max_targets} 를 넘는다")
        ids = [t.id for t in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("targets[].id 가 겹친다")
        for t in self.targets:
            if len(t.urls) > self.limits.max_urls_per_target:
                raise ValueError(f"{t.id}: urls 가 max_urls_per_target 를 넘는다")
        return self


def policy_reasons(order: CollectOrder) -> List[str]:
    """purpose · query_hint 를 owner_policy 로 검사한다 — 걸리면 수집 자체를 거부."""
    text = "\n".join([order.purpose] + [t.query_hint or "" for t in order.targets])
    return hard_stop_reasons(text)


def parse_order(data: dict) -> CollectOrder:
    try:
        order = CollectOrder.model_validate(data)
    except ValidationError as exc:
        raise OrderError(f"ORDER_INVALID: {exc.errors(include_url=False)}") from exc
    reasons = policy_reasons(order)
    if reasons:
        raise OrderError(f"ORDER_POLICY: {reasons}")
    return order


def load_order(path) -> CollectOrder:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderError(f"ORDER_UNREADABLE: {exc}") from exc
    return parse_order(data)


def domain_rule_match(rule: str, host: str, path: str) -> bool:
    """규칙은 `host` 또는 `host/pathprefix`. 호스트는 같거나 하위 도메인이면 맞는다."""
    rhost, _, rpath = rule.strip().partition("/")
    rhost, host = rhost.lower().rstrip("."), host.lower().rstrip(".")   # 「place.naver.com.」 도 같은 호스트다
    if not (host == rhost or host.endswith("." + rhost)):
        return False
    # 경로는 퍼센트 인코딩을 풀어 견준다 — 규칙 「example.com/카페」 와 요청 경로 「/%EC%B9%B4…」 는 같다
    return not rpath or unquote(path).startswith("/" + unquote(rpath))
