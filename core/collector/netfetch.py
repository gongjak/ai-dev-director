"""Director 가 웹 페이지를 받는 유일한 길 (설계 §6 1~6단계 · §7).

URL 형식 → 도메인 정책 → SSRF(주소를 먼저 풀고 그 IP 로 연결을 고정) → robots.txt → 호스트 간격 → 크기 · 시간 제한.
리다이렉트는 3번까지, 매번 1~3단계를 다시 지난다. robots.txt 도 같은 경로로 받는다.
해석기 · 전송은 생성자로 바꿀 수 있다 — 시험은 가짜를 넣고, 기본값은 시험에서 conftest 가 막는다.
"""
import hashlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from core.collector.order import Domains, Limits, domain_rule_match

MAX_REDIRECTS = 3
RESOLVE_TIMEOUT_SEC = 10.0          # getaddrinfo 는 시간 제한이 없다 — 스레드로 묶는다

# 이 결과 코드는 「주장이 틀렸다」가 아니라 「정책 위반」이라 rejected, 나머지 실패는 unverifiable 이다.
ROBOTS_POLICY_CODES = ("BAD_URL", "DOMAIN_DENIED", "DOMAIN_NOT_ALLOWED", "SSRF_BLOCKED", "BUDGET_EXHAUSTED",
                       "BYTES_EXHAUSTED")
REJECT_CODES = {"BAD_URL", "DOMAIN_DENIED", "DOMAIN_NOT_ALLOWED", "SSRF_BLOCKED", "NOT_FOUND_PAGE"}


class TransportError(Exception):
    """연결 실패 · 시간 초과."""


@dataclass
class RawResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    truncated: bool = False


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: Optional[int] = None
    content_type: str = ""
    body: bytes = b""
    ip: str = ""
    reason_code: Optional[str] = None          # None 이면 성공

    @property
    def ok(self) -> bool:
        return self.reason_code is None

    @property
    def outcome(self) -> str:
        if self.ok:
            return "ok"
        return "rejected" if self.reason_code in REJECT_CODES else "unverifiable"


class StdlibTransport:
    """푼 IP 로 연결을 고정하고 SNI · 인증서 검증은 원래 호스트 이름으로 한다 — DNS 재바인딩 창을 없앤다."""

    def request(self, *, scheme: str, host: str, port: int, ip: str, path: str, headers: Dict[str, str],
                timeout: float, max_bytes: int) -> RawResponse:
        deadline = time.monotonic() + timeout
        sock = None

        def arm():
            # 남은 시간을 소켓 제한으로 건다 — TLS · 요청 쓰기 · 한 바이트씩 흘려 보내는 서버가 마감을 넘기지 못하게
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TransportError("timeout")
            sock.settimeout(remain)
            return remain
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
            if scheme == "https":
                arm()
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.sock = sock
            arm()
            conn.request("GET", path, headers=headers)
            arm()
            resp = conn.getresponse()
            chunks, size, truncated = [], 0, False
            while True:
                arm()
                chunk = resp.read1(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    truncated = True
                    break
                chunks.append(chunk)
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            conn.close()
            return RawResponse(resp.status, hdrs, b"".join(chunks), truncated)
        except (OSError, http.client.HTTPException, ValueError) as exc:   # UnicodeError · InvalidURL 도 ValueError
            if sock is not None:
                sock.close()
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc


def _stdlib_resolver(host: str, port: int) -> List[str]:
    """getaddrinfo 를 데몬 스레드에서 돌려 RESOLVE_TIMEOUT_SEC 에서 끊는다(멈춘 해석기가 실행을 붙잡지 못하게)."""
    box: dict = {}

    def work():
        try:
            box["infos"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:          # 스레드 밖으로 그대로 넘긴다
            box["exc"] = exc
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(RESOLVE_TIMEOUT_SEC)
    if th.is_alive():
        raise OSError(f"DNS 시간 초과 {host}")
    if "exc" in box:
        exc = box["exc"]
        raise exc if isinstance(exc, OSError) else OSError(str(exc))
    return sorted({info[4][0] for info in box["infos"]})


# conftest 가 시험 중에 이 둘을 「부르면 예외」로 바꾼다. Fetcher 는 생성 시점에 이 이름을 읽는다.
default_resolver: Callable[[str, int], List[str]] = _stdlib_resolver
default_transport_factory: Callable[[], object] = StdlibTransport


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        elif addr.sixtofour:                                  # 2002::/16 — 안에 든 IPv4 로 본다
            addr = addr.sixtofour
        elif addr in ipaddress.ip_network("64:ff9b::/96"):    # NAT64 — 뒤 32비트가 IPv4
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if addr in ipaddress.ip_network("100.64.0.0/10"):       # CGNAT · Tailscale
        return False
    return addr.is_global and not addr.is_multicast


def check_url_format(url: str) -> Optional[Tuple[str, str, int, str]]:
    """(scheme, host, port, path?query) 또는 None(BAD_URL). IDN 은 punycode 로 바꾼다."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")   # 끝 점은 같은 호스트다
    except UnicodeError:
        return None
    if not host:
        return None
    port = port or (443 if parts.scheme == "https" else 80)
    # ASCII 가 아닌 경로 · 질의는 퍼센트 인코딩 — 이미 인코딩한 「%XX」 는 그대로 둔다
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=?-._~")
    return parts.scheme, host, port, path


def domain_reason(host: str, path: str, domains: Domains) -> Optional[str]:
    if any(domain_rule_match(r, host, path) for r in domains.deny):
        return "DOMAIN_DENIED"
    if domains.allow and not any(domain_rule_match(r, host, path) for r in domains.allow):
        return "DOMAIN_NOT_ALLOWED"
    return None


def precheck(url: str, domains: Domains) -> Optional[str]:
    """§6 1~2단계만(네트워크 없음). URL 찾기 결과를 거를 때 쓴다."""
    parsed = check_url_format(url)
    if not parsed:
        return "BAD_URL"
    return domain_reason(parsed[1], parsed[3].split("?")[0], domains)


@dataclass
class Fetcher:
    limits: Limits
    domains: Domains
    transport: Optional[object] = None
    resolver: Optional[Callable[[str, int], List[str]]] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now_iso: Callable[[], str] = lambda: ""
    log: List[dict] = field(default_factory=list)
    robots_texts: Dict[str, str] = field(default_factory=dict)
    requests_made: int = 0
    bytes_total: int = 0
    _robots: Dict[str, Optional[RobotFileParser]] = field(default_factory=dict)
    _robots_reason: Dict[str, Optional[str]] = field(default_factory=dict)
    _last_at: Dict[str, float] = field(default_factory=dict)
    _delay: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if self.transport is None:
            self.transport = default_transport_factory()
        if self.resolver is None:
            self.resolver = default_resolver

    # ---- 낮은 층: 한 번의 GET(SSRF · 간격 · 예산 포함, robots 제외) ----
    def _get_once(self, url: str, max_bytes: int) -> Tuple[Optional[RawResponse], str, Optional[str]]:
        parsed = check_url_format(url)
        if not parsed:
            return None, "", "BAD_URL"
        scheme, host, port, path = parsed
        reason = domain_reason(host, path.split("?")[0], self.domains)
        if reason:
            return None, "", reason
        try:
            ips = self.resolver(host, port)
        except (OSError, TransportError):
            return None, "", "FETCH_FAILED"
        if not ips or not all(is_public_ip(ip) for ip in ips):
            return None, ",".join(ips or []), "SSRF_BLOCKED"
        if self.requests_made >= self.limits.max_fetch_requests:
            return None, ips[0], "BUDGET_EXHAUSTED"
        if self.bytes_total >= self.limits.max_total_bytes:      # 실행 전체 메모리 · 증거 크기 상한
            return None, ips[0], "BYTES_EXHAUSTED"
        waited = self._wait(host)
        self.requests_made += 1
        headers = {"Host": host if port in (80, 443) else f"{host}:{port}", "User-Agent": self.limits.user_agent,
                   "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5",
                   "Accept-Language": "ko", "Accept-Encoding": "identity"}
        entry = {"url": url, "ip": ips[0], "at_kst": self.now_iso(), "waited_sec": round(waited, 3)}
        try:
            raw = self.transport.request(scheme=scheme, host=host, port=port, ip=ips[0], path=path, headers=headers,
                                         timeout=self.limits.fetch_timeout_sec, max_bytes=max_bytes)
        except TransportError as exc:
            entry.update(status=None, error=str(exc)[:200])
            self.log.append(entry)
            return None, ips[0], "FETCH_FAILED"
        finally:
            self._last_at[host] = self.clock()
        self.bytes_total += len(raw.body)
        entry.update(status=raw.status, bytes=len(raw.body), truncated=raw.truncated,
                     sha256=hashlib.sha256(raw.body).hexdigest())
        self.log.append(entry)
        return raw, ips[0], None

    def _wait(self, host: str) -> float:
        last = self._last_at.get(host)
        if last is None:
            return 0.0
        gap = max(self.limits.min_interval_sec_per_host, self._delay.get(host, 0.0))
        remain = gap - (self.clock() - last)
        if remain > 0:
            self.sleep(remain)
            return remain
        return 0.0

    # ---- robots.txt — 같은 경로로 받는다 ----
    def _robots_check(self, url: str) -> Optional[str]:
        scheme, host, port, _ = check_url_format(url)
        key = f"{scheme}://{host}:{port}"
        if key not in self._robots_reason:
            rurl = f"{scheme}://{host}" + ("" if port in (80, 443) else f":{port}") + "/robots.txt"
            raw, reason = None, None
            for _hop in range(MAX_REDIRECTS + 1):        # robots 리다이렉트도 같은 검사 경로(_get_once)로 따라간다
                raw, _, reason = self._get_once(rurl, max_bytes=512 * 1024)
                if reason or raw is None or raw.status not in (301, 302, 303, 307, 308):
                    break
                loc = raw.headers.get("location")
                nxt = urljoin(rurl, loc) if loc else ""
                if not loc or (rurl.startswith("https:") and nxt.startswith("http:")) or nxt == rurl:
                    reason = "ROBOTS_UNAVAILABLE"
                    break
                rurl = nxt
            else:
                reason = "ROBOTS_UNAVAILABLE"
            parser = None
            if reason in ROBOTS_POLICY_CODES:
                pass
            elif reason or raw is None or raw.status >= 500 or raw.status in (301, 302, 303, 307, 308):
                reason = "ROBOTS_UNAVAILABLE"
            elif raw.status in (401, 403):
                reason = "ROBOTS_DISALLOW"
            elif raw.status in (404, 410) or not (200 <= raw.status < 300):
                reason = None   # 없으면 허용
            elif raw.truncated:
                reason = "ROBOTS_UNAVAILABLE"   # 잘린 robots 는 뒤쪽 Disallow 를 놓친다 — 금지로 본다
            else:
                text = raw.body.decode("utf-8", errors="replace")
                self.robots_texts[host] = text
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                delay = parser.crawl_delay("*")
                if delay:
                    self._delay[host] = float(delay)
            self._robots_reason[key] = reason
            self._robots[key] = parser
        reason = self._robots_reason[key]
        if reason:
            return reason
        parser = self._robots[key]
        if parser is not None and not parser.can_fetch("*", url):
            return "ROBOTS_DISALLOW"
        return None

    # ---- 높은 층: 페이지 받기 ----
    def fetch(self, url: str) -> FetchResult:
        result = FetchResult(url=url)
        current = url
        downgrade = False
        for _hop in range(MAX_REDIRECTS + 1):
            parsed = check_url_format(current)
            if not parsed:
                result.reason_code = "BAD_URL"
                return result
            reason = domain_reason(parsed[1], parsed[3].split("?")[0], self.domains)
            if reason is None:
                try:
                    ips = self.resolver(parsed[1], parsed[2])
                except (OSError, TransportError):
                    ips = None
                if ips is not None and (not ips or not all(is_public_ip(ip) for ip in ips)):
                    reason = "SSRF_BLOCKED"
            if reason is None and downgrade:
                reason = "REDIRECT_DOWNGRADE"        # https → http 는 받지 않는다(정책 · SSRF 검사 뒤에 본다)
            reason = reason or self._robots_check(current)
            if reason:
                result.reason_code = reason
                return result
            raw, ip, reason = self._get_once(current, self.limits.max_bytes_per_page)
            result.ip = ip
            if reason:
                result.reason_code = reason
                return result
            result.status, result.final_url = raw.status, current
            if raw.status in (301, 302, 303, 307, 308):
                loc = raw.headers.get("location")
                if not loc:
                    result.reason_code = "FETCH_FAILED"
                    return result
                nxt = urljoin(current, loc)
                downgrade = current.lower().startswith("https:") and nxt.lower().startswith("http:")
                current = nxt
                continue
            if raw.status in (403, 429):
                result.reason_code = "HTTP_BLOCKED"
            elif raw.status in (404, 410):
                result.reason_code = "NOT_FOUND_PAGE"
            elif not (200 <= raw.status < 300):
                result.reason_code = "FETCH_FAILED"
            elif raw.truncated:
                result.reason_code = "TOO_LARGE"
            else:
                result.content_type = raw.headers.get("content-type", "")
                result.body = raw.body
            return result
        result.reason_code = "TOO_MANY_REDIRECTS"
        return result


def strip_fragment(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
