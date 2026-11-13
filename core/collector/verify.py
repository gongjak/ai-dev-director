"""본문 추출 · 정규화 · 대조 규칙 (설계 §6 6~9단계). LLM 이 없는 결정론 경로다."""
import codecs
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
# 폐업 낱말이 있어도 폐업했다는 말이 아닌 꼴 — 「영업 종료 (시간) (오후) 22:00 · 10시」(마감 시각) · 「[폐업 신고]」(단추) ·
# 「폐업 아닙니다 · 안 했습니다 · 폐업설은 사실무근」(부정). 날짜(「폐업 2024.03.31」)는 시각이 아니다
_CLOSED_HOURS = re.compile(r"^[\s:]*(?:시간|시각)?[\s:]*(?:오전|오후|am|pm)?\s*\d{1,2}\s*(?::\d{2}|시(?![가-힣]))")
_CLOSED_NOT_AFTER = re.compile(r"아니|아닙|아닌|아님|않|안\s*(?:했|함|합|하|된|됐)|사실무근|소문|루머|^설|오보|"
                               r"신고|제보|문의|여부|\?")
_CLOSED_NOT_BEFORE = re.compile(r"(?:\b(?:not|never|no)|n['’]t)\s+$")
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


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_-]+)""", re.I)
_TEXT_CODECS = {"utf-8", "cp949", "euc_kr", "ascii", "latin-1", "iso8859-1", "cp1252", "iso8859-15", "shift_jis",
                "cp932", "euc_jp", "iso2022_jp", "gb2312", "gbk", "gb18030", "big5", "big5hkscs"}
_KO_LEGACY = {"euc-kr", "euckr", "ks-c-5601-1987", "ks_c_5601-1987", "ksc5601", "windows-949", "x-windows-949", "cp949"}


def _decode(body: bytes, content_type: str) -> str:
    """머리글 charset → 본문 앞 2KB 의 meta charset → utf-8 → cp949 순서. euc-kr 은 상위 집합 cp949 로 읽는다.
    선언이 없으면 utf-8 · cp949 를 엄격하게 시도하고, 둘 다 안 되면 utf-8(대체 문자) — 깨진 바이트 하나로 페이지 전체를
    cp949 로 읽지 않는다."""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    cs = m.group(1) if m else None
    if not cs:
        mm = _META_CHARSET.search(body[:2048])
        cs = mm.group(1).decode("ascii") if mm else None
    if cs and cs.lower() in _KO_LEGACY:
        cs = "cp949"
    try:
        cs = codecs.lookup(cs).name if cs else None
    except LookupError:
        cs = None
    if cs in _TEXT_CODECS:                 # unicode_escape · idna · utf-16(ASCII meta 와 모순) 같은 선언은 믿지 않는다
        try:
            return body.decode(cs, errors="replace")
        except (LookupError, UnicodeError):
            pass
    for enc in ("utf-8", "cp949"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            pass
    return body.decode("utf-8", errors="replace")


def extract_text(body: bytes, content_type: str) -> Optional[PageText]:
    """지원하지 않는 종류면 None(UNSUPPORTED_CONTENT)."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in _TEXT_TYPES:
        return None
    decoded = _decode(body, content_type)
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


def normalize_lines(text: str) -> str:
    """normalize 와 같되 줄바꿈은 남긴다 — 폐업 판정에서 줄 끝이 문장 끝이다."""
    return "\n".join(x for x in (normalize(line) for line in (text or "").splitlines()) if x)


def normalize_address(text: str) -> str:
    t = normalize(text)
    t = re.sub(r"[‐‑‒–—−]", "-", t)
    for long, short in _SIDO:
        # 시도 낱말 하나일 때만 — 「제주도로」 「경기도로」 같은 도로명 안의 글자는 두지 않는다
        t = re.sub(re.escape(long) + r"(?![가-힣])", short, t)
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
    """tok 를 start 부터 찾되, tok 가 숫자로 끝나면 바로 뒤가 숫자 · 「-숫자」인 자리(「회나무로4」↔「회나무로49」 ·
    「회나무로49」↔「회나무로49-1」)는 건너뛴다. 숫자로 시작하면 바로 앞이 숫자 · 「숫자-」인 자리도 건너뛴다."""
    pos = body.find(tok, start)
    while pos != -1:
        end = pos + len(tok)
        # 「압구정로108」↔「압구정로108번길5」 · 「압구정로10」↔「압구정로10길5」 · 「을지로3」↔「을지로3가 10」도 다른 곳이다
        after_ok = not (tok[-1].isdigit() and end < len(body) and (
            body[end].isdigit() or (body[end] == "-" and end + 1 < len(body) and body[end + 1].isdigit())
            or _ROAD_TAIL.match(body, end)))
        before_ok = not (tok[0].isdigit() and pos > 0 and (
            body[pos - 1].isdigit() or (body[pos - 1] == "-" and pos > 1 and body[pos - 2].isdigit())))
        if after_ok and before_ok:
            return pos
        pos = body.find(tok, pos + 1)
    return -1


_GAP_OK = re.compile(r"[\W_]*")
_ROAD_TAIL = re.compile(r"번길|길|가(?![가-힣])")


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
            # 토큰 사이에는 공백 · 문장부호만 — 글자 · 숫자가 끼면 다른 주소를 꿰맨 것일 수 있다
            if nxt == -1 or nxt + len(tok) - start > WINDOW or not _GAP_OK.fullmatch(body[pos:nxt]):
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


MIN_NAME_CHARS = 2


def _name_patterns(name: str) -> List["re.Pattern"]:
    """이름 전체와, 끝의 괄호(「(청담점)」)를 뗀 이름. 문장부호를 뺀 길이가 MIN_NAME_CHARS 미만이면 쓰지 않는다."""
    out = []
    for cand in dict.fromkeys([name or "", re.sub(r"\s*[(（\[].*?[)）\]]\s*$", "", name or "")]):
        c = _compact(cand)
        if len(c) >= MIN_NAME_CHARS:
            out.append(re.compile(r"[\W_]*".join(re.escape(ch) for ch in c)))
    return out


def name_near(name: str, body: str, start: int, end: int, window: int = NAME_WINDOW) -> bool:
    """body(이미 정규화한 본문)의 [start, end) 앞뒤 window 자 안에 이름이 있는가."""
    near = body[max(0, start - window):end + window]
    return any(p.search(near) for p in _name_patterns(name))


def address_specific(value: str) -> bool:
    """도로명 + 건물번호 또는 동 · 리 + 지번이 있어야 한다 — 「서울」 「서울 강남구」는 어디에나 있다."""
    return road_key(value) is not None or _JIBUN.search(normalize_address(value)) is not None


def _host_in(host: str, hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def check_official_url(value: str, final_url: str, known_name: str, page: str, domains: Domains,
                       official_domain: str = "") -> Optional[str]:
    """None 이면 받는다. 「NEEDS_HUMAN_REVIEW」 는 거부가 아니라 검증 불가 — 블랙리스트에 없다는 것은 공식이라는 증거가 아니다.
    지시서가 그 대상의 known.official_domain 으로 적은 호스트(또는 하위 도메인)일 때만 받는다."""
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
    od = (official_domain or "").strip().lower().rstrip(".")
    if not od or is_public_suffix(od) or not _host_in(parsed[1], (od,)):
        return "NEEDS_HUMAN_REVIEW"
    return None


# 공용 접미사 — 누구나 그 아래에 등록할 수 있어 「공식 도메인」이 될 수 없다(「co.kr」 이면 evil.co.kr 도 맞는다).
# 새 의존성 없이 흔한 꼴만 막는다: 점 없는 이름(TLD) · 2자 국가 TLD 아래 2단계 등록 이름 · .kr 지역 이름
_SLD = {"co", "or", "go", "ac", "ne", "re", "pe", "ms", "hs", "es", "sc", "kg", "mil", "com", "net", "org", "gov",
        "edu", "ltd", "plc", "nhs", "gen", "biz", "info"}
_KR_REGIONS = {"seoul", "busan", "daegu", "incheon", "gwangju", "daejeon", "ulsan", "sejong", "gyeonggi", "gangwon",
               "chungbuk", "chungnam", "jeonbuk", "jeonnam", "gyeongbuk", "gyeongnam", "jeju"}


def is_public_suffix(domain: str) -> bool:
    labels = [x for x in domain.split(".") if x]
    if len(labels) < 2:
        return True
    if len(labels) == 2 and len(labels[1]) == 2 and (labels[0] in _SLD or (labels[1] == "kr" and labels[0] in _KR_REGIONS)):
        return True
    return len(labels) == 3 and labels[2] == "kr" and labels[1] in _KR_REGIONS and labels[0] in _SLD


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


FIELDS_NEED_NAME = ("address", "official_url", "status")


def _closed_affirmative(body: str, a: int, b: int) -> bool:
    """폐업 낱말 뒤 문장 끝(줄 끝 · 「. ! ]」 · 최대 30자)까지에 마감 시각 · 부정 · 신고 단추 꼴이 없고,
    앞에 not · n't 가 없어야 폐업했다는 말이다. 날짜 안의 점(「2024.03.31」)은 문장 끝이 아니다."""
    after = re.split(r"(?<!\d)[.!]|[.!](?!\d)|[\n\]]", body[b:b + 30])[0]
    return not (_CLOSED_HOURS.search(after) or _CLOSED_NOT_AFTER.search(after)
                or _CLOSED_NOT_BEFORE.search(body[max(0, a - 10):a]))


@dataclass
class Verdict:
    outcome: str                 # accepted · rejected · unverifiable
    reason_code: Optional[str] = None
    match_rule: Optional[str] = None
    window: str = ""
    value: Optional[str] = None


def verify_claim(field_name: str, value, final_url: str, known_name: str, page: PageText,
                 domains: Domains, official_domain: str = "") -> Verdict:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Verdict("rejected", "NO_VALUE")
    if not isinstance(value, str) or len(value) > 500 or re.search(r"[\x00-\x1f\x7f]", value):
        return Verdict("rejected", "BAD_VALUE")
    if field_name == "address" and not has_hangul(value):   # 한국어 주소만 받는다 — 영문 표기는 대조 · 병합 기준이 다르다
        return Verdict("rejected", "NOT_KOREAN_ADDRESS")
    if field_name in FIELDS_NEED_NAME and len(_compact(known_name or "")) < MIN_NAME_CHARS:
        return Verdict("rejected", "NAME_TOO_SHORT")       # 한 글자 이름은 어디에나 있다
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
        reason = check_official_url(value, final_url, known_name, text, domains, official_domain)
        if reason == "NEEDS_HUMAN_REVIEW":
            return Verdict("unverifiable", reason, value=value.strip())
        if reason:
            return Verdict("rejected", reason)
        return Verdict("accepted", match_rule="exact", value=value.strip())
    if field_name == "status":
        body = normalize_lines(text)
        if normalize(value) != "closed":
            return Verdict("rejected", "STATUS_NOT_CLOSED")    # 「영업 중」은 받지 않는다
        hits = [(m.start(), m.end()) for w in CLOSED_WORDS for m in re.finditer(re.escape(normalize(w)), body)
                if _closed_affirmative(body, m.start(), m.end())]
        if not hits:
            return Verdict("rejected", "VALUE_NOT_ON_PAGE")
        if not any(name_near(known_name, body, a, b) for a, b in hits):   # 남의 가게 폐업 문구를 막는다
            return Verdict("rejected", "NAME_NOT_NEAR_VALUE")
        return Verdict("accepted", match_rule="closed_word", value="closed")
    return Verdict("rejected", "UNKNOWN_FIELD")


# 번호 뒤가 「가」 낱말이면 도로명이 아니라 동 이름이다(「을지로3가」 · 「종로1가」)
_ROAD_KEY = re.compile(r"([가-힣0-9]+(?:번길|로|길))(\d+(?:-\d+)?)(?!\d)(?!가(?![가-힣]))")
_SIDO_SHORT = {short for _, short in _SIDO}


def road_key(value: str) -> Optional[str]:
    """도로명 + 건물번호(「회나무로49」). 층 · 호 · 지번 표기 차이를 무시하고 같은 곳인지 비교하는 열쇠다. 없으면 None."""
    m = _ROAD_KEY.search(normalize_address(value))
    return m.group(1) + m.group(2) if m else None


def sido_of(value: str) -> Optional[str]:
    """정규화한 주소의 첫 토큰이 시도 약칭이면 그것."""
    first = normalize_address(value).split(" ")[0] if value else ""
    return first if first in _SIDO_SHORT else None


def districts_of(value: str) -> set:
    """시 · 군 · 구 낱말들(「성남시」 「분당구」). 시도 약칭(「대구」 · 「세종」)은 빼고 본다."""
    return {t for t in normalize_address(value).split(" ")
            if re.fullmatch(r"[가-힣]+(?:시|군|구)", t) and t not in _SIDO_SHORT}


def _districts_disjoint(a: set, b: set) -> bool:
    """시 · 군 · 구 단위마다 따로 본다 — 양쪽에 같은 단위가 있는데 겹치지 않으면 다른 곳(성남시 분당구 ↔ 성남시 수정구)."""
    return any({x for x in a if x.endswith(u)} and {x for x in b if x.endswith(u)}
               and not ({x for x in a if x.endswith(u)} & {x for x in b if x.endswith(u)}) for u in "시군구")


def has_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def conflict_keys(accepted: List[dict]) -> set:
    """같은 대상 · 필드에 받아들인 값이 서로 다른 곳을 가리키면 그 (target_id, field).
    주소는 도로명 + 건물번호 열쇠로 비교한다 — 「회나무로 49」와 「회나무로 49 1층」은 같은 곳이다.
    열쇠가 없는 값(지번만 있는 주소)은 비교에서 뺀다. 열쇠가 하나도 없으면 정규화한 값 전체로 비교한다.
    시도는 양쪽 다 적혀 있을 때만 비교한다 — 한쪽에 없으면 도로명 + 번호만 본다.
    시 · 군 · 구도 양쪽에 적혀 있고 겹치는 것이 하나도 없으면 다른 곳이다(강남구 중앙로 10 ↔ 종로구 중앙로 10).
    official_url 은 normalize_url 로 비교한다(끝 「/」 · 호스트 대소문자)."""
    seen: Dict[tuple, set] = {}
    sidos: Dict[tuple, set] = {}
    dists: Dict[tuple, list] = {}
    for c in accepted:
        k = (c["target_id"], c["field"])
        if c["field"] == "address":
            rk = road_key(c["value"])
            norm = rk or ("raw:" + normalize_address(c["value"]))
            if rk and sido_of(c["value"]):
                sidos.setdefault(k, set()).add(sido_of(c["value"]))
            if rk and districts_of(c["value"]):
                dists.setdefault(k, []).append(districts_of(c["value"]))
        elif c["field"] == "official_url":
            norm = normalize_url(c["value"])
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
        elif any(_districts_disjoint(a, b) for i, a in enumerate(dists.get(k, [])) for b in dists[k][i + 1:]):
            out.add(k)
    return out
