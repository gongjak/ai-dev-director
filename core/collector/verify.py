"""본문 추출 · 정규화 · 대조 규칙 (설계 §6 6~9단계). LLM 이 없는 결정론 경로다."""
import codecs
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from core.collector.netfetch import check_url_format, domain_reason
from core.collector.order import Domains

WINDOW = 200
LINK_WINDOW = 300                             # 같은 행 · 항목 안에서 링크 앞뒤로 이름을 찾는 글자 수(S2)
MAX_LINKS = 3000                              # 페이지 한 장에서 적는 링크 수
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
_SKIP_TAGS = {"script", "style", "noscript", "template"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
               "header", "footer", "ul", "ol", "table", "dd", "dt"}
# 공식 페이지로 받지 않는 호스트 — 포털 · 지도 · 블로그 · SNS · 여행 안내 · 뉴스 · 대기열 · 기부 몰.
# 받기(robots · 속도 · 거부 목록)는 바꾸지 않는다 — 이 목록은 「그 호스트를 공식 사이트로 받느냐」만 정한다.
# visitkorea.or.kr 은 공공 관광 포털이라 .or.kr 이어도 축제 자신의 사이트가 아니다(S1 을 지나지 못하게 여기서 막는다).
# xn--ok0b236bp0a.com 은 여행 안내 「여기유」다 — 제목이 「<축제 이름> | 여기유」 꼴이다(2026-11-25 실제 수집)
NON_OFFICIAL_HOSTS = ("blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "map.naver.com", "place.naver.com",
                      "instagram.com", "facebook.com", "tistory.com", "youtube.com", "twitter.com", "x.com",
                      "map.kakao.com", "place.map.kakao.com", "siksinhot.com", "mangoplate.com", "diningcode.com",
                      "naver.com", "daum.net", "kakao.com", "google.com", "brunch.co.kr", "velog.io", "medium.com",
                      "blogspot.com", "wordpress.com", "modoo.at", "threads.net", "tiktok.com", "namu.wiki",
                      "wikipedia.org", "visitkorea.or.kr", "ktriptips.com", "koreacharts.com", "xn--ok0b236bp0a.com",
                      "tripadvisor.com", "tripadvisor.co.kr", "trip.com", "klook.com", "myrealtrip.com",
                      "interpark.com", "yes24.com", "ticketlink.co.kr", "queue-it.net", "wegive.co.kr",
                      "korea.kr", "imaeil.com", "yna.co.kr", "newsis.com", "news1.kr", "chosun.com", "joongang.co.kr",
                      "donga.com", "hani.co.kr", "khan.co.kr", "mk.co.kr", "hankyung.com", "ohmynews.com",
                      "nocutnews.co.kr", "youthassembly.kr")
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
    h1: str = ""                                                   # 첫 <h1> 의 글(로고 그림의 alt 포함) — full() 에 넣지 않는다
    # (href 원문, 링크의 글 + 그 링크와 같은 표의 행 · 목록 항목의 글) — 줄 밖의 글은 싣지 않는다
    links: List[Tuple[str, str]] = field(default_factory=list)

    def full(self) -> str:
        return "\n".join([self.title, self.text] + self.jsonld_addresses)


H1_MAX_CHARS = 120                            # 이보다 긴 첫 <h1> 은 제목이 아니다(닫지 않았거나 본문을 감쌌다)
_ROW_TAGS = ("tr", "li")                      # S2 — 이름과 링크가 같은 줄(표의 행 · 목록의 항목)에 있는지 본다
_ROW_BOUNDS = {"tr": ("tr", "table"), "li": ("li", "ul", "ol")}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self.meta_desc = ""
        self.jsonld: List[str] = []
        self._skip = 0
        self._in_title = False
        self._title_done = False              # 첫 <title> 만 제목이다
        self._svg = 0                         # <svg><title> 은 그림 설명이지 페이지 제목이 아니다
        self._in_ld = False
        self._ld_buf: List[str] = []
        self._n = 0                           # parts 에 쌓인 글자 수 — 링크 자리를 적는다
        self.h1_parts: List[str] = []
        self._h1 = 0                          # 0 아직 없음 · 1 첫 h1 안 · 2 끝남
        # 링크마다 {href, pos(시작 자리), end(</a> 자리), row(그 링크를 감싼 가장 안쪽 tr · li 의 [시작, 끝])}
        self.anchors: List[dict] = []
        self._a: Optional[dict] = None        # 열려 있는 <a>
        self._rows: List[list] = []           # 열려 있는 표 · 목록 · 행 — [tag, 시작 자리, 그 안의 링크들]

    def _add(self, text: str) -> None:
        self.parts.append(text)
        self._n += len(text)

    def _close_a(self) -> None:
        if self._a is not None:
            self._a["end"] = self._n
            self._a = None

    def _pop_row(self) -> None:
        tag, start, anchors = self._rows.pop()
        for a in anchors:
            a["row"] = (start, self._n)

    def _close_row(self, tag: str) -> None:
        """tag 가 열려 있으면 거기까지 닫는다 — 닫는 표지를 빼먹은 <li> · <tr> 은 그 위의 것이 닫힐 때 같이 닫힌다."""
        if any(r[0] == tag for r in self._rows):
            while self._rows[-1][0] != tag:
                self._pop_row()
            self._pop_row()

    def _open_row(self, tag: str) -> None:
        if tag in _ROW_BOUNDS:                # 새 <li> · <tr> — 같은 목록 · 표의 앞 항목이 열려 있으면 닫는다
            for r in reversed(self._rows):
                if r[0] in _ROW_BOUNDS[tag]:
                    if r[0] == tag:
                        self._close_row(tag)
                    break
        self._rows.append([tag, self._n, []])

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._ld_buf = True, []
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "svg":
            self._svg += 1
        elif tag == "title" and not self._skip and not self._svg and not self._title_done:
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta_desc = a.get("content") or ""
        # 리뷰(agy 3) — 숨긴 구역(noscript · template)의 h1 · 로고 alt · 링크는 보이는 글이 아니다. 판정 재료로 쓰지 않는다
        if not self._skip:
            if tag == "h1" and self._h1 == 0:
                self._h1 = 1
            elif tag == "img" and a.get("alt"):
                if self._h1 == 1:             # 로고 그림으로 된 제목 — <h1><a><img alt="축제 이름">
                    self.h1_parts.append(a["alt"])
                if self._a is not None:       # 그림으로 된 링크의 글
                    self._a["alt"].append(a["alt"])
            elif tag == "a":
                self._close_a()
                if a.get("href") and len(self.anchors) < MAX_LINKS:
                    self._a = {"href": a["href"].strip()[:2048], "pos": self._n, "end": None, "row": None, "alt": []}
                    self.anchors.append(self._a)
                    row = next((r for r in reversed(self._rows) if r[0] in _ROW_TAGS), None)
                    if row is not None:
                        row[2].append(self._a)
            if tag in ("tr", "li", "table", "ul", "ol"):
                self._open_row(tag)
        if tag in _BLOCK_TAGS:
            self._add("\n")

    def handle_endtag(self, tag):
        if self._in_ld and tag == "script":
            self._in_ld = False
            self.jsonld.append("".join(self._ld_buf))
            return
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "svg" and self._svg:
            self._svg -= 1
        elif tag == "title" and self._in_title:
            self._in_title, self._title_done = False, True
        elif self._skip:
            return
        elif tag == "h1" and self._h1 == 1:
            self._h1 = 2
        elif tag == "a":
            self._close_a()
        elif tag in ("tr", "li", "table", "ul", "ol"):
            self._close_row(tag)

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self._add(data)
            if self._h1 == 1:
                self.h1_parts.append(data)

    def close(self):
        super().close()
        self._close_a()
        while self._rows:
            self._pop_row()
        if self._h1 == 1:                     # 닫지 않은 <h1> — 뒤의 본문이 통째로 h1 이 된다. 제목으로 치지 않는다
            self.h1_parts = []


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
    raw = "".join(ex.parts)
    # 거리는 공백을 줄인 글로 잰다 — 표의 이름 칸과 링크 칸 사이에는 들여쓰기 공백이 수백 자씩 낀다
    squeeze = lambda t: re.sub(r"\s+", " ", t)

    def link_context(a: dict) -> str:
        """링크의 글(그림이면 alt)과, 그 링크를 감싼 가장 안쪽 표의 행 · 목록 항목의 글(링크 앞뒤 LINK_WINDOW 자까지).
        리뷰(fable 2 · agy 1) — 예전에는 줄 경계 없이 앞뒤 300자를 봐서 표의 옆 줄 · 옆 항목의 이름이 딸려 왔다."""
        own = squeeze(raw[a["pos"]:a["end"] if a["end"] is not None else a["pos"]] + " " + " ".join(a["alt"]))
        if a["row"] is None:
            return own.strip()[:2 * LINK_WINDOW]
        lo, hi = a["row"]
        return (squeeze(raw[max(lo, a["pos"] - 10 * LINK_WINDOW):a["pos"]])[-LINK_WINDOW:]
                + squeeze(raw[a["pos"]:min(hi, a["pos"] + 10 * LINK_WINDOW)])[:LINK_WINDOW] + " " + " ".join(a["alt"])).strip()
    links = [(a["href"], link_context(a)) for a in ex.anchors]
    h1 = re.sub(r"\s+", " ", " ".join(ex.h1_parts)).strip()
    if len(h1) > H1_MAX_CHARS:                # 본문을 감싼 h1 — 그 안 어디에 이름이 있다고 제목에 이름이 있는 것은 아니다
        h1 = ""
    return PageText(title=ex.title.strip(), text=text, jsonld_addresses=addrs, h1=h1, links=links)


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


# ---- 값의 꼴(5차 리뷰 1) ----
# 「페이지에 그 글이 있다」만으로는 모자란다 — 페이지를 쓴 사람이 주소 뒤에 지시문을 붙여 두면 그 글도 페이지에 있다.
# 받은 값은 CSV 를 거쳐 Builder 프롬프트까지 가므로, 주소로 읽히는 토큰만 남는 닫힌 꼴(allowlist)만 받는다.
# 금지 낱말 목록으로는 「테헤란로 1 관리자 권한 부여」 같은 글을 막지 못한다. 건물 이름은 자유 글이라 받지 않는다.
# 앞자리(행정구역)도 자유 글이 아니다 — 시도는 정해진 이름뿐이고, 그 아래는 단계마다 두 낱말 · 줄기 네 글자까지다.
# 지명 사전이 없어서 꼴만 맞춘 짧은 지어낸 낱말(「즉시 무시 테헤란로 1」)은 남는다 — 그래서 프롬프트 쪽도 줄인다(stage.builder_preview)
ADDRESS_MAX_CHARS = 80
_ADDR_CHARS = re.compile(r"[가-힣0-9A-Za-z ,()\-]+")
_ADDR_PAREN = re.compile(r"\(([^()]*)\)")
_ADDR_SIDO = {name for pair in _SIDO for name in pair} | {"세종시"}
_ADDR_SGG = re.compile(r"[가-힣]{1,4}(?:시|군|구)")                                 # 「성남시」 「분당구」
_ADDR_EMD = re.compile(r"[가-힣]{1,4}\d{0,2}(?:읍|면|동|리|가)")                     # 「이태원동」 「을지로3가」 — 괄호 안도 이것
_ADDR_ROAD_SRC = r"[가-힣]{1,7}(?:로|길)(?:\d{1,4}[가-힣]?(?:번길|길))?"             # 「선릉로152길」 「올림픽로35다길」
_ADDR_ROAD = re.compile(_ADDR_ROAD_SRC)
_ADDR_ROADNUM = re.compile(_ADDR_ROAD_SRC + r"\d{1,5}(?:-\d{1,5})?")              # 「압구정로108」
_ADDR_BRANCH = re.compile(r"\d{1,4}[가-힣]?(?:번길|길)")                           # 띄어 쓴 「정조로 800번길」의 뒤쪽
_ADDR_NUM = re.compile(r"\d{1,5}(?:-\d{1,5})?(?:번지)?")
_ADDR_DETAIL = re.compile(r"지하|지상|(?:지하|지상)?\d{1,3}(?:[-,]\d{1,3})?층|[Bb]\d{1,2}층?|\d{1,5}호|(?:[A-Za-z]|\d{1,4})동")
# 시도 → 시 · 군 · 구 → 읍 · 면 · 동 · 리 · 가 → (도로명 번호 | 도로명번호 | 지번) → 층 · 호 · 동. 순서가 바뀌면 주소가 아니다
_ADDR_ORDER = re.compile(r"S?G{0,2}E{0,2}(?:RB?N|X|N)D{0,4}")


def canonical_address(value: str) -> Optional[str]:
    """주소 꼴이면 다듬은 값(NFKC · 줄표 통일 · 공백 하나), 아니면 None. CSV 에는 이 값이 놓인다 —
    폭 없는 글자 · 전각 글자가 원문 그대로 프롬프트까지 가지 않게 한다."""
    t = unicodedata.normalize("NFKC", value or "").translate(_ZW)
    t = re.sub(r"\s+", " ", re.sub(r"[‐‑‒–—−]", "-", t)).strip()
    if not t or len(t) > ADDRESS_MAX_CHARS or not _ADDR_CHARS.fullmatch(t):
        return None
    groups = _ADDR_PAREN.findall(t)
    rest = _ADDR_PAREN.sub(" ", t)
    if "(" in rest or ")" in rest or len(groups) > 1:
        return None
    for g in groups:
        inner = [x for x in re.split(r"[ ,]+", g) if x]
        if not 1 <= len(inner) <= 2 or not all(_ADDR_EMD.fullmatch(x) for x in inner):
            return None
    kinds = []
    # 쉼표는 구분자다. 숫자 사이의 쉼표(「1,2층」)만 토큰 안에 둔다
    for tok in (x for x in re.split(r" +|,(?!\d)", rest) if x):
        if tok in _ADDR_SIDO:
            kinds.append("S")
            continue
        for kind, pat in (("X", _ADDR_ROADNUM), ("R", _ADDR_ROAD), ("B", _ADDR_BRANCH), ("N", _ADDR_NUM), ("D", _ADDR_DETAIL),
                          ("G", _ADDR_SGG), ("E", _ADDR_EMD)):
            if pat.fullmatch(tok):
                kinds.append(kind)
                break
        else:
            return None
    return t if _ADDR_ORDER.fullmatch("".join(kinds)) else None


URL_MAX_CHARS = 200
_URL_CHARS = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+")       # 공백 · 따옴표 · 백틱 · 꺾쇠가 없는 URL 만


def csv_url(url: str) -> str:
    """CSV 의 source_url 칸에 놓을 값 — URL 글자만으로 된 200자 안의 URL 이면 그대로, 아니면 origin, 그것도 안 되면 빈 값.
    리뷰(fable 5) — 넘어간 곳의 URL(Location)은 남이 고른 글이고 CSV 는 Builder · 리뷰어 프롬프트로 간다."""
    u = (url or "").strip()
    if len(u) <= URL_MAX_CHARS and _URL_CHARS.fullmatch(u):
        return u
    parsed = check_url_format(u)
    if not parsed:
        return ""
    return f"{parsed[0]}://{parsed[1]}" + ("" if parsed[2] in (80, 443) else f":{parsed[2]}")


def _host_in(host: str, hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def check_official_url(value: str, final_url: str, known_name: str, page: str, domains: Domains,
                       official_domain: str = "") -> Optional[str]:
    """None 이면 받는다. 「NEEDS_HUMAN_REVIEW」 는 거부가 아니라 검증 불가 — 블랙리스트에 없다는 것은 공식이라는 증거가 아니다.
    지시서가 그 대상의 known.official_domain 으로 적은 호스트(또는 하위 도메인)일 때만 받는다."""
    # 5차 리뷰 1 — URL 칸에는 URL 만. 공백 · 따옴표 · 백틱 · 꺾쇠가 들었거나 너무 길면 받지 않는다
    if not isinstance(value, str) or len(value.strip()) > URL_MAX_CHARS or not _URL_CHARS.fullmatch(value.strip()):
        return "BAD_URL"
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


# 무료 호스팅 · 사이트 제작 서비스의 이름 — 누구나 그 아래에 하위 이름을 만든다(리뷰 fable 1). 공용 접미사와 같이 다룬다:
# 이름 자체는 공식 도메인이 될 수 없고, 그 아래 호스트는 자동 판정(S1 · S2 · S3)으로 받지 않는다(지시서가 적어 주면 받는다)
FREE_HOSTING_SUFFIXES = ("imweb.me", "github.io", "gitlab.io", "cafe24.com", "mycafe24.com", "notion.site", "wixsite.com",
                         "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app", "firebaseapp.com",
                         "weebly.com", "creatorlink.net", "oopy.io", "herokuapp.com", "quv.kr", "strikingly.com",
                         "webflow.io", "glitch.me", "r2.dev", "sites.google.com", "godaddysites.com", "square.site",
                         "framer.website", "carrd.co", "my.canva.site", "super.site", "softr.app", "bubbleapps.io")


def on_free_hosting(host: str) -> bool:
    return _host_in((host or "").lower().rstrip("."), FREE_HOSTING_SUFFIXES)


def is_public_suffix(domain: str) -> bool:
    labels = [x for x in domain.split(".") if x]
    if len(labels) < 2 or ".".join(labels) in FREE_HOSTING_SUFFIXES:
        return True
    if len(labels) == 2 and len(labels[1]) == 2 and (labels[0] in _SLD or (labels[1] == "kr" and labels[0] in _KR_REGIONS)):
        return True
    return len(labels) == 3 and labels[2] == "kr" and labels[1] in _KR_REGIONS and labels[0] in _SLD


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


# ---- 공식 URL 자동 판정 §6.4 (2026-11-25 공작 결정) ----
# 값은 모델이 아니라 코드가 만든다 — Director 가 접속한 최종 URL 의 origin 이다(첫 화면에는 제 URL 이 적혀 있지 않다).
# 기본 조건(이름이 페이지에 있다 · 거부 · 비공식 호스트가 아니다 · 공용 접미사가 아니다 · 사이트의 첫 화면이다)을
# 모두 지나고, 강한 신호 S1 · S1D · S2 · S3 가운데 하나가 있어야 받는다. 그 밖에는 사람이 본다(NEEDS_HUMAN_REVIEW).
# 리뷰(2026-11-25 · agy Neon high + fable) 뒤 — S1 은 .go.kr 뿐이다. .or.kr · .re.kr 은 등록 심사가 느슨해서(비영리 「단체」면
# 받는다) 제목의 이름만으로는 받지 않고 그 해의 축제 날짜가 더 있어야 한다(S1D). 영리 도메인은 S2 이거나 S3 다.
GOV_SUFFIX = "go.kr"                                          # S1 · S2 — 정부 · 지자체 등록 이름. 「*.gb.go.kr」 도 그 아래다
PUBLIC_DOMAIN_SUFFIXES = ("or.kr", "re.kr")                   # S1D — 비영리 · 연구 등록 이름(날짜가 더 있어야 한다)
_HOME_PATH = re.compile(r"/?(?:(?:index|main|home|default)(?:\.[a-z]{2,5})?)?", re.I)
HOME_REDIRECT_MAX_SEGMENTS = 2                # 루트에서 넘어간 곳을 첫 화면으로 치는 깊이(「/intro/view」 · 「/kor/main.do」)
_PROGRAM_WORDS = (r"(?:(?<!보안 )(?<!제휴 )(?<!설치 )(?<!백신 )프로그램(?!\s*(?:설치|다운로드))|행사\s*일정|세부\s*일정|일정표|"
                  r"공연\s*일정|라인업|타임테이블|\b(?:program|programme|schedule|line-?up|timetable)\b)")
# 「주최 · 주관」 은 낱말로 서 있고(「주관적인」 · 「주최측」 이 아니다) 줄 머리이거나 구분 기호(| / · ,) 뒤여야 하며, 쌍점이 따라야 한다.
# 그 뒤 30자 안(같은 칸)에 기관 낱말이 낱말째로 있어야 한다 — 쌍점 없는 문장 속의 「반드시」 「안내도」 「변동시」 는 이제 걸리지 않는다.
# 남는 것: 쌍점 바로 뒤에 그런 낱말이 홀로 서면(「주최 : 반드시 확인」) 지명과 꼴이 같아 걸린다 — 지명 사전이 없다. S3 는 날짜도 요구한다
_ORG_WORD = (r"(?:[가-힣]{0,12}(?:재단|위원회|공사|문화원|시청|군청|구청|도청|협회|진흥원)"
             r"|[가-힣]{1,5}(?:특별자치도|특별자치시|광역시|특별시|시|군|구|도))")
_ORGANIZER = re.compile(r"(?:^|[|/·,])[ \t]*(?:주최|주관)(?:[ \t]*[/·][ \t]*(?:주최|주관))?[ \t]*[:：]"
                        r"[^\n|:：]{0,30}?(?<![가-힣])" + _ORG_WORD + r"(?![가-힣])", re.M)


_NOT_FESTIVAL_DATE = re.compile(r"모집|접수|신청|공모|응모|예매|등록|공고|마감|발표")


def _bare(host: str) -> str:
    """「www.」 를 뗀 호스트 — 같은 사이트인지 견줄 때."""
    h = (host or "").lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def is_home_page(requested_url: str, final_url: str) -> bool:
    """받은 페이지가 그 사이트의 첫 화면인가 — 최종 경로가 루트(또는 index · main)이거나, 루트를 청했는데 같은 호스트
    안에서 두 단계까지 넘어간 곳이다(「/」 → 「/intro/view」). 깊은 쪽의 제목에 이름이 있다고 origin 이 그 축제의 사이트는 아니다 —
    시청 사이트의 축제 소개 쪽(suwon.go.kr/…/sw-visitsuwon-03-01.jsp)이 그 예다."""
    req, fin = urlsplit(requested_url or ""), urlsplit(final_url or "")
    if not fin.query and _HOME_PATH.fullmatch(fin.path or ""):
        return True
    # 리뷰(fable 3) — 넘어간 곳이 깊으면(「/tour/festival/view.do」 · 질의가 붙었다) 첫 화면이 아니다. 루트가 깊은 쪽으로
    # 넘기는 사이트에서 그 쪽의 제목만으로 origin 을 받지 않는다
    shallow = not fin.query and len([x for x in (fin.path or "").split("/") if x]) <= HOME_REDIRECT_MAX_SEGMENTS
    return (shallow and not req.query and _HOME_PATH.fullmatch(req.path or "") is not None
            and _bare(req.hostname or "") == _bare(fin.hostname or ""))


def _name_in(name: str, text: str) -> bool:
    """공백 · 문장부호를 무시하고 이름이 있는가(「대구 치맥페스티벌」 = 「대구치맥페스티벌」)."""
    return any(p.search(normalize(text)) for p in _name_patterns(name))


def _name_whole_in(name: str, text: str) -> bool:
    """이름이 「낱말째로」 있는가 — 앞뒤에 한글이 맞닿아 있으면 다른 이름의 일부다(리뷰 fable 3: 「장미축제」 ↔ 「서울장미축제」 ·
    「장미축제추진위원회」). 조사가 붙은 꼴(「장미축제에 오신…」)도 세지 않는다 — 닫힌 쪽으로 틀린다. 숫자 · 영문 · 기호는 맞닿아도 된다.
    띄어 쓴 「서울 장미축제」 는 막지 못한다(낱말 경계만 본다)."""
    t = normalize(text)
    for p in _name_patterns(name):
        for m in p.finditer(t):
            before, after = t[m.start() - 1:m.start()], t[m.end():m.end() + 1]
            if not re.match(r"[가-힣]", before or " ") and not re.match(r"[가-힣]", after or " "):
                return True
    return False


def official_signals(page: PageText, year: str) -> List[str]:
    """S3 의 신호 — 전부 글자 대조다. year 는 대상 연도(「2026」). 네 자리 연도가 아니면 신호는 없다.
    DATES     그 해의 축제 날짜: 날짜 범위(「2026년 5월 1일 ~ 5월 10일」 — 뒤쪽도 월 · 일이 있어야 한다)이거나 「기간 · 일시 · 일정 ·
              개최」 표지 바로 뒤의 날짜. 게시판 글 날짜(「2026.04.13」 · 「2026.04.13 - 1차 안내」)와, 앞 14자 · 뒤 14자 안에
              「모집 · 접수 …」 가 있는 기간은 세지 않는다.
    ORGANIZER 줄 머리(또는 구분 기호 뒤)의 「주최 : 」 · 「주관 : 」 뒤 30자 안에 기관 낱말(지자체 · 재단 · 위원회 · 공사 …)
    PROGRAM   그 해의 프로그램 · 일정 차림: 낱말 앞뒤 40자 안에 연도가 수로(앞뒤에 숫자가 없이) 있다."""
    if not re.fullmatch(r"(?:19|20)\d\d", year or ""):       # 빈 연도는 모든 날짜에 맞는다 — 열린 쪽으로 틀리지 않게
        return []
    y = rf"(?<!\d){re.escape(year)}"
    text = normalize(page.full())
    md = r"\d{1,2}\s*[.\-/월]\s*\d{1,2}"
    date = rf"{y}\s*[.\-/년]\s*{md}\s*일?\.?"
    # 범위의 뒤쪽 — 「05.10」 · 「5월 10일」 · 「2026.05.10」 · 「10일」. 숫자 하나(「- 1차 안내」)는 날짜가 아니다
    tail = rf"(?:(?:\d{{4}}\s*[.\-/년]\s*)?{md}|\d{{1,2}}\s*일)(?!\d)"
    ranged = date + r"\s*(?:\([^)]{1,4}\))?\s*(?:\d{1,2}:\d{2}\s*)?[~\-–—]\s*\(?" + tail
    labeled = r"(?:기간|일시|일정|개최)\s*[:|]?\s*" + date
    out = []
    # 「모집 기간 · 접수 2026.04.13 ~ 04.20」 · 「2026.04.13 ~ 04.20 참가 부스 모집」 은 축제 날짜가 아니다
    if any(not _NOT_FESTIVAL_DATE.search(text[max(0, m.start() - 14):m.start()])
           and not _NOT_FESTIVAL_DATE.search(text[m.end():m.end() + 14])
           for m in re.finditer(f"(?:{ranged})|(?:{labeled})", text)):
        out.append("DATES")
    if _ORGANIZER.search(normalize_lines(page.full())):
        out.append("ORGANIZER")
    # 리뷰(fable 1) — 「제목 · h1 에 연도 + 본문에 낱말」 갈래는 뺐다. 차림에 「일정표」 하나 있는 블로그도 그 꼴이다
    yb = y + r"(?!\d)"
    if re.search(rf"{yb}.{{0,40}}?{_PROGRAM_WORDS}|{_PROGRAM_WORDS}.{{0,40}}?{yb}", text):
        out.append("PROGRAM")
    return out


def _port_key(parts) -> Optional[int]:
    """기본 포트(80 · 443 · 없음)는 None — http 와 https 는 같은 사이트로 본다. 다른 포트는 다른 서버다."""
    try:
        port = parts.port
    except ValueError:
        return -1
    return None if port in (None, 80, 443) else port


def _gov_link_source(host: str, name: str, final_url: str, gov_pages) -> str:
    """S2 — 같은 수집에서 받은 .go.kr 페이지 가운데, 이 호스트(포트까지)로 건 링크가 있고 그 링크의 글이나 같은 행 · 항목에
    이름이 낱말째로 있는 것의 URL."""
    port = _port_key(urlsplit(final_url))
    for src_url, src in gov_pages or []:
        src_host = (urlsplit(src_url).hostname or "").lower()
        if not _host_in(src_host, (GOV_SUFFIX,)) or _bare(src_host) == _bare(host):
            continue
        for href, context in src.links:
            try:
                target = urlsplit(urljoin(src_url, href))
            except ValueError:
                continue
            if (_bare((target.hostname or "").lower()) == _bare(host) and _port_key(target) == port
                    and _name_whole_in(name, context)):
                return src_url
    return ""


@dataclass
class OfficialDecision:
    outcome: str                              # accepted · rejected · unverifiable
    value: str = ""
    reason_code: Optional[str] = None
    detail: str = ""                          # 사람이 볼 때의 사유 낱말(코드가 정한 것)
    match_rule: Optional[str] = None          # S1 · S1D · S2 · S3 · exact(지시서의 official_domain)
    quote: str = ""                           # 이름이 든 제목 · h1 — 페이지 글이다. 증거 jsonl 에만 남긴다
    signals: List[str] = field(default_factory=list)
    link_source: str = ""                     # S2 — 링크를 건 .go.kr 페이지


def decide_official_url(requested_url: str, final_url: str, known: Dict[str, str], page: PageText, domains: Domains,
                        year: str, gov_pages=None) -> OfficialDecision:
    """받은 페이지 한 장이 known.name 의 공식 사이트인가. gov_pages 는 같은 수집에서 받은 [(final_url, PageText)]."""
    name = (known or {}).get("name", "")
    parsed = check_url_format(final_url)
    if not parsed:
        return OfficialDecision("rejected", reason_code="BAD_URL")
    host = parsed[1]
    # 리뷰(fable 6) — 값은 다듬은 호스트로 만든다(punycode · 끝 점 없음). 한글 호스트 · 「host.」 가 그대로 CSV 로 가지 않게
    netloc = host if parsed[2] in (80, 443) else f"{host}:{parsed[2]}"
    origin = f"{parsed[0]}://{netloc}"
    if domain_reason(host, parsed[3].split("?")[0], domains):
        return OfficialDecision("rejected", reason_code="DOMAIN_DENIED")
    if _host_in(host, NON_OFFICIAL_HOSTS) or is_public_suffix(host):
        return OfficialDecision("rejected", reason_code="NOT_OFFICIAL_HOST")
    if len(_compact(name)) < MIN_NAME_CHARS:
        return OfficialDecision("rejected", reason_code="NAME_TOO_SHORT")
    if not _name_in(name, page.full() + "\n" + page.h1):
        return OfficialDecision("rejected", reason_code="NAME_NOT_ON_PAGE")
    home = is_home_page(requested_url, final_url)
    deep = normalize_url(f"{origin}{parsed[3]}")
    shaped = len(deep) <= URL_MAX_CHARS and _URL_CHARS.fullmatch(deep) is not None
    value = origin if home or not shaped else deep
    quote = next((x for x in (page.title, page.h1) if x and _name_whole_in(name, x)), "")[:300]
    signals = official_signals(page, year)
    base = dict(value=value, quote=quote, signals=signals)
    od = (known.get("official_domain") or "").strip().lower().rstrip(".")
    if od and not is_public_suffix(od) and _host_in(host, (od,)):         # 지시서가 적은 도메인 — 예전 규칙 그대로
        return OfficialDecision("accepted", match_rule="exact", **base)
    if not home:
        return OfficialDecision("unverifiable", reason_code="NEEDS_HUMAN_REVIEW", detail="NOT_HOME_PAGE", **base)
    if on_free_hosting(host):                 # 누구나 만드는 하위 이름 — 글이 그럴듯해도 자동으로 받지 않는다
        return OfficialDecision("unverifiable", reason_code="NEEDS_HUMAN_REVIEW", detail="FREE_HOSTING", **base)
    # 강한 신호는 전부 「제목 · 첫 h1 에 이름이 낱말째로 있다」 위에 선다(리뷰 fable 2 — S2 도 그렇다)
    if not quote:
        return OfficialDecision("unverifiable", reason_code="NEEDS_HUMAN_REVIEW", detail="NAME_NOT_IN_TITLE", **base)
    gov = _host_in(host, (GOV_SUFFIX,))
    if gov:
        return OfficialDecision("accepted", match_rule="S1", **base)
    if _host_in(host, PUBLIC_DOMAIN_SUFFIXES) and "DATES" in signals:
        return OfficialDecision("accepted", match_rule="S1D", **base)
    src = _gov_link_source(host, name, final_url, gov_pages)      # 받는 쪽이 .go.kr 이면 여기 오지 않는다(S1 이다)
    if src:
        return OfficialDecision("accepted", match_rule="S2", link_source=src, **base)
    if "DATES" in signals and len(signals) >= 2:                  # 그 해의 축제 날짜 + 하나 더
        return OfficialDecision("accepted", match_rule="S3", **base)
    return OfficialDecision("unverifiable", reason_code="NEEDS_HUMAN_REVIEW", detail="NO_STRONG_SIGNAL", **base)


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
        # 5차 리뷰 1 — 주소로 읽히는 토큰만 남는 꼴이어야 한다(뒤에 붙은 지시문 · 경로 · URL 을 막는다)
        shaped = canonical_address(value)
        if shaped is None:
            return Verdict("rejected", "ADDRESS_BAD_SHAPE")
        spans = match_address_spans(value, text)
        if not spans:
            return Verdict("rejected", "VALUE_NOT_ON_PAGE")
        body = normalize_address(text)
        # 여러 식당이 실린 페이지에서 남의 주소를 붙이지 못하게 — 그 자리 가까이에 대상 이름이 있어야 한다
        near = [sp for sp in spans if name_near(known_name, body, sp[1], sp[2])]
        if not near:
            return Verdict("rejected", "NAME_NOT_NEAR_VALUE")
        rule, a, b = near[0]
        return Verdict("accepted", match_rule=rule, window=body[a:b], value=shaped)
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
