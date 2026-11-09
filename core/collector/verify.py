"""본문 추출 · 정규화 · 대조 규칙 (설계 §6 6~9단계). LLM 이 없는 결정론 경로다."""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.collector.netfetch import check_url_format, domain_reason
from core.collector.order import Domains

WINDOW = 200
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
_SKIP_TAGS = {"script", "style", "noscript", "template"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
               "header", "footer", "ul", "ol", "table", "dd", "dt"}
# 공식 페이지로 받지 않는 호스트 — 포털 · 지도 · 블로그 · SNS
NON_OFFICIAL_HOSTS = ("blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "map.naver.com", "place.naver.com",
                      "instagram.com", "facebook.com", "tistory.com", "youtube.com", "twitter.com", "x.com",
                      "map.kakao.com", "place.map.kakao.com", "siksinhot.com", "mangoplate.com", "diningcode.com")
CLOSED_WORDS = ("폐업", "영업 종료", "영업종료", "영구 폐점", "영구폐점", "closed permanently", "permanently closed")
# 시도 약칭 — 긴 이름부터 바꾼다
_SIDO = [("서울특별시", "서울"), ("서울시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"),
         ("광주광역시", "광주"), ("대전광역시", "대전"), ("울산광역시", "울산"), ("세종특별자치시", "세종"),
         ("경기도", "경기"), ("강원특별자치도", "강원"), ("강원도", "강원"), ("충청북도", "충북"), ("충청남도", "충남"),
         ("전북특별자치도", "전북"), ("전라북도", "전북"), ("전라남도", "전남"), ("경상북도", "경북"), ("경상남도", "경남"),
         ("제주특별자치도", "제주"), ("제주도", "제주")]


@dataclass
class PageText:
    title: str = ""
    text: str = ""
    jsonld_addresses: List[str] = field(default_factory=list)

    def full(self) -> str:
        return "\n".join([self.title, self.text] + self.jsonld_addresses)


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self.meta_desc = ""
        self.jsonld: List[str] = []
        self._skip = 0
        self._in_title = False
        self._in_ld = False
        self._ld_buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._ld_buf = True, []
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta_desc = a.get("content") or ""
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._in_ld and tag == "script":
            self._in_ld = False
            self.jsonld.append("".join(self._ld_buf))
            return
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _ld_addresses(raw: str) -> List[str]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    out: List[str] = []

    def walk(node):
        if isinstance(node, dict):
            # 같은 객체의 name 을 주소 앞에 붙인다 — 이름 근접 대조(§6.2)가 JSON-LD 에서도 돈다
            name = node.get("name") if isinstance(node.get("name"), str) else ""
            for k, v in node.items():
                if k in ("address", "streetAddress") and isinstance(v, str):
                    out.append(f"{name} {v}".strip())
                elif k == "address" and isinstance(v, dict):
                    out.append(" ".join([name] + [str(v.get(x, "")) for x in ("addressRegion", "addressLocality",
                                                                                "streetAddress") if v.get(x)]).strip())
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(data)
    return [x for x in out if x.strip()]


def _charset(content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    return m.group(1) if m else "utf-8"


def extract_text(body: bytes, content_type: str) -> Optional[PageText]:
    """지원하지 않는 종류면 None(UNSUPPORTED_CONTENT)."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in _TEXT_TYPES:
        return None
    try:
        decoded = body.decode(_charset(content_type), errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    if ctype in ("text/plain", "application/json"):
        return PageText(text=decoded)
    ex = _Extractor()
    ex.feed(decoded)
    ex.close()
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(ex.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if ex.meta_desc:
        text = ex.meta_desc + "\n" + text
    addrs = [a for raw in ex.jsonld for a in _ld_addresses(raw)]
    return PageText(title=ex.title.strip(), text=text, jsonld_addresses=addrs)


# ---- 정규화 §6.1 ----
_ZW = dict.fromkeys(map(ord, "​‌‍﻿"))


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_ZW)
    return re.sub(r"\s+", " ", t).strip().lower()


def normalize_address(text: str) -> str:
    t = normalize(text)
    t = re.sub(r"[‐‑‒–—−]", "-", t)
    for long, short in _SIDO:
        t = t.replace(long, short)
    t = re.sub(r"[(),]", " ", t)                        # 괄호 안은 별도 토큰 · 쉼표는 구분자
    t = re.sub(r"(번길|로|길)\s+(?=\d)", r"\1", t)      # 「압구정로 108」 → 「압구정로108」
    return re.sub(r"\s+", " ", t).strip()


def normalize_url(url: str) -> str:
    p = urlsplit((url or "").strip())
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() != "fbclid"])
    return urlunsplit((p.scheme.lower(), netloc, p.path.rstrip("/"), query, ""))


# ---- 대조 §6.2 ----
NAME_WINDOW = 400
_JIBUN = re.compile(r"[가-힣0-9]+(?:동|리|가)\s*\d+(?:-\d+)?")


def _find_bounded(body: str, tok: str, start: int) -> int:
    """tok 를 start 부터 찾되, tok 가 숫자로 끝나면 바로 뒤가 숫자인 자리(「회나무로4」↔「회나무로49」)는 건너뛴다.
    숫자로 시작하면 바로 앞이 숫자인 자리도 건너뛴다."""
    pos = body.find(tok, start)
    while pos != -1:
        end = pos + len(tok)
        after_ok = not (tok[-1].isdigit() and end < len(body) and body[end].isdigit())
        before_ok = not (tok[0].isdigit() and pos > 0 and body[pos - 1].isdigit())
        if after_ok and before_ok:
            return pos
        pos = body.find(tok, pos + 1)
    return -1


def match_address_spans(value: str, page: str) -> List[Tuple[str, int, int]]:
    """정규화한 본문에서 값이 맞는 자리 전부 — [(rule, start, end)]. 정확 일치가 있으면 그것만."""
    v, body = normalize_address(value), normalize_address(page)
    if not v:
        return []
    out: List[Tuple[str, int, int]] = []
    pos = _find_bounded(body, v, 0)
    while pos != -1:
        out.append(("exact", pos, pos + len(v)))
        pos = _find_bounded(body, v, pos + 1)
    if out:
        return out
    tokens = v.split(" ")
    start = _find_bounded(body, tokens[0], 0)
    while start != -1:
        pos, ok = start + len(tokens[0]), True
        for tok in tokens[1:]:
            nxt = _find_bounded(body, tok, pos)
            if nxt == -1 or nxt + len(tok) - start > WINDOW:
                ok = False
                break
            pos = nxt + len(tok)
        if ok:
            out.append(("tokens", start, pos))
        start = _find_bounded(body, tokens[0], start + 1)
    return out


def match_address(value: str, page: str) -> Tuple[Optional[str], str]:
    """(match_rule, window 원문). 못 찾으면 (None, '')."""
    spans = match_address_spans(value, page)
    if not spans:
        return None, ""
    rule, a, b = spans[0]
    return rule, normalize_address(page)[a:b]


def _compact(text: str) -> str:
    """이름 비교용 — 공백 · 문장부호를 뺀 소문자(「구스테이크 528」 = 「구스테이크528」)."""
    return re.sub(r"[\W_]+", "", normalize(text))


def _name_pattern(name: str) -> Optional["re.Pattern"]:
    c = _compact(name)
    if not c:
        return None
    return re.compile(r"[\W_]*".join(re.escape(ch) for ch in c))


def name_near(name: str, body: str, start: int, end: int, window: int = NAME_WINDOW) -> bool:
    """body(이미 정규화한 본문)의 [start, end) 앞뒤 window 자 안에 이름이 있는가."""
    pat = _name_pattern(name)
    if pat is None:
        return False
    return pat.search(body[max(0, start - window):end + window]) is not None


def address_specific(value: str) -> bool:
    """도로명 + 건물번호 또는 동 · 리 + 지번이 있어야 한다 — 「서울」 「서울 강남구」는 어디에나 있다."""
    return road_key(value) is not None or _JIBUN.search(normalize_address(value)) is not None


def _host_in(host: str, hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def check_official_url(value: str, final_url: str, known_name: str, page: str, domains: Domains) -> Optional[str]:
    parsed = check_url_format(value)
    if not parsed:
        return "BAD_URL"
    if normalize_url(value) not in (normalize_url(final_url), normalize_url(_origin(final_url))):
        return "URL_NOT_SOURCE"
    if domain_reason(parsed[1], parsed[3].split("?")[0], domains):
        return "DOMAIN_DENIED"
    if _host_in(parsed[1], NON_OFFICIAL_HOSTS):
        return "NOT_OFFICIAL_HOST"
    if not known_name or normalize(known_name) not in normalize(page):
        return "NAME_NOT_ON_PAGE"
    return None


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


@dataclass
class Verdict:
    outcome: str                 # accepted · rejected
    reason_code: Optional[str] = None
    match_rule: Optional[str] = None
    window: str = ""
    value: Optional[str] = None


def verify_claim(field_name: str, value, final_url: str, known_name: str, page: PageText,
                 domains: Domains) -> Verdict:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Verdict("rejected", "NO_VALUE")
    if not isinstance(value, str) or len(value) > 500 or re.search(r"[\x00-\x1f\x7f]", value):
        return Verdict("rejected", "BAD_VALUE")
    if field_name == "address" and not has_hangul(value):   # 한국어 주소만 받는다 — 영문 표기는 대조 · 병합 기준이 다르다
        return Verdict("rejected", "NOT_KOREAN_ADDRESS")
    text = page.full()
    if field_name == "address":
        if not address_specific(value):
            return Verdict("rejected", "ADDRESS_TOO_VAGUE")
        spans = match_address_spans(value, text)
        if not spans:
            return Verdict("rejected", "VALUE_NOT_ON_PAGE")
        body = normalize_address(text)
        # 여러 식당이 실린 페이지에서 남의 주소를 붙이지 못하게 — 그 자리 가까이에 대상 이름이 있어야 한다
        near = [sp for sp in spans if name_near(known_name, body, sp[1], sp[2])]
        if not near:
            return Verdict("rejected", "NAME_NOT_NEAR_VALUE")
        rule, a, b = near[0]
        return Verdict("accepted", match_rule=rule, window=body[a:b], value=value.strip())
    if field_name == "official_url":
        reason = check_official_url(value, final_url, known_name, text, domains)
        if reason:
            return Verdict("rejected", reason)
        return Verdict("accepted", match_rule="exact", value=value.strip())
    if field_name == "status":
        body = normalize(text)
        if normalize(value) != "closed":
            return Verdict("rejected", "STATUS_NOT_CLOSED")    # 「영업 중」은 받지 않는다
        hits = [(m.start(), m.end()) for w in CLOSED_WORDS for m in re.finditer(re.escape(normalize(w)), body)]
        if not hits:
            return Verdict("rejected", "VALUE_NOT_ON_PAGE")
        if not any(name_near(known_name, body, a, b) for a, b in hits):   # 남의 가게 폐업 문구를 막는다
            return Verdict("rejected", "NAME_NOT_NEAR_VALUE")
        return Verdict("accepted", match_rule="closed_word", value="closed")
    return Verdict("rejected", "UNKNOWN_FIELD")


_ROAD_KEY = re.compile(r"([가-힣0-9]+(?:번길|로|길))(\d+(?:-\d+)?)(?!\d)")
_SIDO_SHORT = {short for _, short in _SIDO}


def road_key(value: str) -> Optional[str]:
    """도로명 + 건물번호(「회나무로49」). 층 · 호 · 지번 표기 차이를 무시하고 같은 곳인지 비교하는 열쇠다. 없으면 None."""
    m = _ROAD_KEY.search(normalize_address(value))
    return m.group(1) + m.group(2) if m else None


def sido_of(value: str) -> Optional[str]:
    """정규화한 주소의 첫 토큰이 시도 약칭이면 그것."""
    first = normalize_address(value).split(" ")[0] if value else ""
    return first if first in _SIDO_SHORT else None


def has_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def conflict_keys(accepted: List[dict]) -> set:
    """같은 대상 · 필드에 받아들인 값이 서로 다른 곳을 가리키면 그 (target_id, field).
    주소는 도로명 + 건물번호 열쇠로 비교한다 — 「회나무로 49」와 「회나무로 49 1층」은 같은 곳이다.
    열쇠가 없는 값(지번만 있는 주소)은 비교에서 뺀다. 열쇠가 하나도 없으면 정규화한 값 전체로 비교한다.
    시도는 양쪽 다 적혀 있을 때만 비교한다 — 한쪽에 없으면 도로명 + 번호만 본다."""
    seen: Dict[tuple, set] = {}
    sidos: Dict[tuple, set] = {}
    for c in accepted:
        k = (c["target_id"], c["field"])
        if c["field"] == "address":
            rk = road_key(c["value"])
            norm = rk or ("raw:" + normalize_address(c["value"]))
            if rk and sido_of(c["value"]):
                sidos.setdefault(k, set()).add(sido_of(c["value"]))
        else:
            norm = normalize(c["value"])
        seen.setdefault(k, set()).add(norm)
    out = set()
    for k, vals in seen.items():
        keyed = {v for v in vals if not v.startswith("raw:")}
        if len(keyed if keyed else vals) > 1:
            out.add(k)
        elif len(sidos.get(k, ())) > 1:          # 같은 도로명 · 번호라도 시도가 다르면 다른 곳(서울 중구 ↔ 부산 중구)
            out.add(k)
    return out
