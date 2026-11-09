"""netfetch — SSRF · robots · 간격 · 크기 · 리다이렉트. 사설 IP 거부를 먼저 본다."""
import pytest

from _collector_fakes import HTML, FakeTransport, fake_resolver
from core.collector import netfetch
from core.collector.netfetch import Fetcher
from core.collector.order import DEFAULT_USER_AGENT, Domains, Limits


class Clock:
    def __init__(self):
        self.t, self.slept = 100.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def mk(routes, limits=None, domains=None):
    clock = Clock()
    tr = FakeTransport(routes)
    f = Fetcher(limits or Limits(), domains or Domains(), transport=tr, resolver=fake_resolver, clock=clock,
                sleep=clock.sleep)
    return f, tr, clock


def test_ssrf_private_ip_blocked_first():
    f, tr, _ = mk({})
    assert f.fetch("http://evil.test/x").reason_code == "SSRF_BLOCKED"
    assert f.fetch("http://tail.test/x").reason_code == "SSRF_BLOCKED"       # 100.64/10
    assert f.fetch("http://mixed.test/x").reason_code == "SSRF_BLOCKED"      # 하나라도 사설
    assert tr.requests == []


def test_redirect_to_private_blocked():
    f, _, _ = mk({"https://example.com/a": (302, {"Location": "http://evil.test/"}, b"")})
    r = f.fetch("https://example.com/a")
    assert r.reason_code == "SSRF_BLOCKED" and r.outcome == "rejected"


def test_bad_schemes():
    f, _, _ = mk({})
    for u in ("file:///etc/passwd", "ftp://example.com/", "https://user:pw@example.com/", "gopher://x"):
        assert f.fetch(u).reason_code == "BAD_URL"


def test_domain_deny_path_prefix():
    f, _, _ = mk({})
    d = Domains(deny=["siksinhot.com/P/"])
    assert netfetch.domain_reason("www.siksinhot.com", "/P/123", d) == "DOMAIN_DENIED"
    assert netfetch.domain_reason("www.siksinhot.com", "/theme/magazine/2770", d) is None
    assert netfetch.domain_reason("x.com", "/", Domains(allow=["example.com"])) == "DOMAIN_NOT_ALLOWED"


def test_ok_with_user_agent_and_pinned_ip():
    f, tr, _ = mk({"https://example.com/a": (200, HTML, "<p>hi</p>")})
    r = f.fetch("https://example.com/a")
    assert r.ok and r.body == b"<p>hi</p>" and r.ip == "93.184.216.34"
    page_req = tr.requests[-1]
    assert page_req["headers"]["User-Agent"] == DEFAULT_USER_AGENT and page_req["ip"] == "93.184.216.34"
    assert tr.requests[0]["url"] == "https://example.com/robots.txt"


def test_too_many_redirects():
    routes = {f"https://example.com/{i}": (301, {"Location": f"/{i + 1}"}, b"") for i in range(5)}
    f, _, _ = mk(routes)
    assert f.fetch("https://example.com/0").reason_code == "TOO_MANY_REDIRECTS"


def test_size_cap():
    f, _, _ = mk({"https://example.com/big": (200, HTML, b"x" * 5000)}, limits=Limits(max_bytes_per_page=1024))
    assert f.fetch("https://example.com/big").reason_code == "TOO_LARGE"


@pytest.mark.parametrize("status,code", [(403, "HTTP_BLOCKED"), (429, "HTTP_BLOCKED"), (404, "NOT_FOUND_PAGE"),
                                         (500, "FETCH_FAILED")])
def test_status_codes(status, code):
    f, _, _ = mk({"https://example.com/a": (status, HTML, b"")})
    assert f.fetch("https://example.com/a").reason_code == code


def test_connection_failure():
    f, _, _ = mk({"https://example.com/a": (200, HTML, OSError("reset"))})
    assert f.fetch("https://example.com/a").reason_code == "FETCH_FAILED"


def test_robots_disallow_and_unavailable_and_404():
    f, _, _ = mk({"https://example.com/robots.txt": (200, {}, "User-agent: *\nDisallow: /P/\n"),
                  "https://example.com/ok": (200, HTML, b"ok")})
    assert f.fetch("https://example.com/P/1").reason_code == "ROBOTS_DISALLOW"
    assert f.fetch("https://example.com/ok").ok
    f2, _, _ = mk({"https://example.com/robots.txt": (503, {}, b"")})
    assert f2.fetch("https://example.com/a").reason_code == "ROBOTS_UNAVAILABLE"
    f3, _, _ = mk({"https://example.com/robots.txt": (403, {}, b"")})
    assert f3.fetch("https://example.com/a").reason_code == "ROBOTS_DISALLOW"
    f4, _, _ = mk({"https://example.com/a": (200, HTML, b"ok")})   # robots 404 → 허용
    assert f4.fetch("https://example.com/a").ok


def test_interval_and_crawl_delay():
    f, _, clock = mk({"https://example.com/robots.txt": (200, {}, "User-agent: *\nCrawl-delay: 7\n"),
                      "https://example.com/a": (200, HTML, b"a"), "https://example.com/b": (200, HTML, b"b")})
    f.fetch("https://example.com/a")
    f.fetch("https://example.com/b")
    assert clock.slept and all(s == pytest.approx(7) for s in clock.slept)
    g, _, c2 = mk({"https://example.com/a": (200, HTML, b"a"), "https://example.com/b": (200, HTML, b"b")})
    g.fetch("https://example.com/a")
    g.fetch("https://example.com/b")
    assert c2.slept and all(s == pytest.approx(3) for s in c2.slept)


def test_budget():
    f, _, _ = mk({"https://example.com/a": (200, HTML, b"a")}, limits=Limits(max_fetch_requests=1))
    assert f.fetch("https://example.com/a").reason_code == "BUDGET_EXHAUSTED"   # robots 가 1건을 썼다


def test_default_network_is_blocked_in_tests():
    f = Fetcher(Limits(), Domains())
    with pytest.raises(AssertionError, match="REAL_NETWORK"):
        f.fetch("https://example.com/")
