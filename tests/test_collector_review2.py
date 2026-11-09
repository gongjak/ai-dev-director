"""두 번째 리뷰 지적(2026-11-09 · codex · fable) 회귀 시험 — 고치기 전에 실패하는 것을 먼저 확인했다."""
import json
import os
import socket
import threading
import time

import pytest

from _collector_fakes import HTML, FakeTransport, fake_resolver
from core.collector import cli, netfetch, outputs, providers
from core.collector.netfetch import Fetcher, StdlibTransport, TransportError, check_url_format, domain_reason, precheck
from core.collector.order import Domains, Limits, OrderError, load_order, parse_order
from core.collector.providers import HaikuCollector, ProviderFatal
from core.collector.verify import PageText, conflict_keys, extract_text, normalize_address, verify_claim

D = Domains()
NAME = "구스테이크528"


def addr(value, text, name=NAME):
    return verify_claim("address", value, "https://example.com/a", name, PageText(text=text), D)


def status(text, name=NAME):
    return verify_claim("status", "closed", "https://example.com/a", name, PageText(text=text), D)


def mk(routes, limits=None, domains=None):
    tr = FakeTransport(routes)
    return Fetcher(limits or Limits(), domains or Domains(), transport=tr, resolver=fake_resolver,
                   clock=lambda: 0.0, sleep=lambda s: None), tr


def order_dict(targets, **over):
    o = {"version": 1, "order_id": "o1", "purpose": "식당 주소", "targets": targets,
         "output": {"schema": "steak-sources-v1", "path": "data/sources/web.csv"},
         "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    o.update(over)
    return o


def write_order(tmp_path, targets, **over):
    p = tmp_path / "order.json"
    p.write_text(json.dumps(order_dict(targets, **over), ensure_ascii=False), encoding="utf-8")
    return p


def jl(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


class ScriptedProvider(HaikuCollector):
    """call() 을 대본대로 — dict 면 성공, 문자열이면 그 분류로 fatal."""

    def __init__(self, script):
        super().__init__(30, 10)
        self.script = list(script)

    def call(self, kind, argv_tail, prompt):
        if self.fatal:
            raise ProviderFatal(self.fatal)
        self.calls_made += 1
        step = self.script.pop(0)
        if isinstance(step, str):
            self.fatal = step
            raise ProviderFatal(step)
        self.successes += 1
        return step


def run_with(tmp_path, targets, provider, routes, fetcher=None):
    order_path = write_order(tmp_path, targets)
    order = load_order(order_path)
    f = fetcher or Fetcher(order.limits, order.domains, transport=FakeTransport(routes), resolver=fake_resolver,
                           clock=lambda: 0.0, sleep=lambda s: None)
    code, summary = cli.run_collect(order_path, tmp_path / "out", fetcher=f, provider=provider)
    return code, summary, tmp_path / "out" / "o1"


# ---- 1 폐업 판정 — 영업 종료 시각 · 신고 단추 · 부정 ----
@pytest.mark.parametrize("text", [
    f"{NAME}\n영업 시작 11:30 · 영업 종료 22:00 · 라스트오더 21:00",
    f"{NAME} 서울 강남구 압구정로 108\n[폐업 신고] [정보 수정 제안]",
    f"{NAME}은 폐업 아닙니다",
    f"{NAME}은 폐업하지 않았습니다",
    f"{NAME} is not permanently closed",
])
def test_closed_word_not_affirmative_rejected(text):
    assert status(text).outcome == "rejected"


def test_closed_affirmative_still_accepted():
    assert status(f"{NAME} 은 폐업 했습니다").outcome == "accepted"
    assert status(f"{NAME} 2024년 영업 종료").outcome == "accepted"


# ---- 2 토큰 규칙이 두 주소를 꿰매지 않는다 ----
def test_tokens_do_not_stitch_two_addresses():
    page = f"{NAME} 매장 안내\n강남점: 서울 강남구 테헤란로 10\n마포점: 서울 마포구 독막로 1"
    assert addr("서울 강남구 독막로 1", page).outcome == "rejected"
    page2 = "Target 서울 강남구 압구정로 108. Other Shop 서울 마포구 독막로 1"
    assert addr("서울 강남구 독막로 1", page2, name="Target").outcome == "rejected"


def test_tokens_do_not_borrow_floor_from_other_shop():
    page = f"{NAME} 서울 강남구 압구정로 108\n다른집 서울 강남구 선릉로 21 3층"
    assert addr("서울 강남구 압구정로 108 3층", page).outcome == "rejected"


# ---- 3 하이픈 경계 ----
def test_hyphen_boundary():
    assert addr("서울 용산구 회나무로 49", f"{NAME} 서울 용산구 회나무로 49-1").outcome == "rejected"
    assert addr("서울 용산구 이태원동 211", f"{NAME} 서울 용산구 이태원동 211-33").outcome == "rejected"
    assert addr("서울 용산구 회나무로 49-1", f"{NAME} 서울 용산구 회나무로 49-1").outcome == "accepted"


# ---- 4 ASCII 가 아닌 경로 ----
def test_non_ascii_path_percent_encoded():
    parsed = check_url_format("https://ko.wikipedia.org/wiki/울프강?q=스테이크")
    assert parsed[3].isascii() and parsed[3].startswith("/wiki/%EC%9A%B8")
    # 이미 인코딩한 것은 다시 인코딩하지 않는다
    assert check_url_format("https://example.com/a%20b")[3] == "/a%20b"


def test_transport_unicode_error_is_transport_error():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        with pytest.raises(TransportError):
            StdlibTransport().request(scheme="http", host="localhost", port=port, ip="127.0.0.1", path="/울프강",
                                      headers={}, timeout=2.0, max_bytes=1000)
    finally:
        srv.close()


def test_cli_writes_outputs_on_unexpected_url_error(tmp_path):
    class Boom:
        log, robots_texts, requests_made = [], {}, 0

        def fetch(self, url):
            raise RuntimeError("뜻밖의 오류")
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    code, s, out = run_with(tmp_path, targets, ScriptedProvider([]), {}, fetcher=Boom())
    assert (out / "summary.json").exists()
    assert [r["reason_code"] for r in jl(out / "unverifiable.jsonl")] == ["INTERNAL_ERROR"]


# ---- 5 meta charset ----
def test_meta_charset_euc_kr():
    html = f"<html><head><meta charset='euc-kr'></head><body>{NAME} 서울 강남구 압구정로 108</body></html>"
    page = extract_text(html.encode("cp949"), "text/html")
    assert verify_claim("address", "서울 강남구 압구정로 108", "https://example.com/a", NAME, page, D).outcome == "accepted"
    html2 = f'<meta http-equiv="Content-Type" content="text/html; charset=EUC-KR"><p>{NAME}</p>'
    assert NAME in extract_text(html2.encode("cp949"), "text/html").text


def test_utf8_with_one_bad_byte_stays_utf8():
    page = extract_text(f"<p>{NAME} 서울 강남구 압구정로 108</p>".encode("utf-8") + b"\xff", "text/html")
    assert NAME in page.text


def test_undeclared_cp949_fallback():
    page = extract_text(f"<p>{NAME} 서울 강남구 압구정로 108</p>".encode("cp949"), "text/html")
    assert NAME in page.text


# ---- 6 official_url 모순은 정규화한 URL 로 ----
def test_official_url_conflict_uses_normalize_url():
    rows = [{"target_id": "t", "field": "official_url", "value": "https://a.example.com/"},
            {"target_id": "t", "field": "official_url", "value": "https://A.example.com"}]
    assert conflict_keys(rows) == set()


# ---- 7 을지로3가 ----
def test_eulji_ga_not_road_key():
    assert addr("서울 중구 을지로3가", f"{NAME} 서울 중구 을지로3가").reason_code == "ADDRESS_TOO_VAGUE"
    rows = [{"target_id": "t", "field": "address", "value": "서울 중구 을지로3가 10"},
            {"target_id": "t", "field": "address", "value": "서울 중구 을지로3가 295-3"}]
    assert conflict_keys(rows) == {("t", "address")}


# ---- 8 시도 바꾸기가 도로명을 먹지 않는다 ----
def test_sido_replace_keeps_road_names():
    assert "제주도로1" in normalize_address("제주특별자치도 서귀포시 제주도로 1")
    assert "경기도로3" in normalize_address("광주광역시 서구 경기도로 3")
    assert normalize_address("본문 서울특별시 강남구") == "본문 서울 강남구"


# ---- 9 region 은 시도가 아니면 known.sido ----
def test_region_falls_back_to_known_sido():
    assert outputs.region_of("강남구 압구정로 108", "서울") == "서울"
    assert outputs.region_of("서울특별시 강남구 압구정로 108", "") == "서울"


# ---- 10 이름 — 지점 괄호 · 너무 짧은 이름 · 빈 이름 ----
def test_branch_suffix_name():
    r = addr("서울 강남구 압구정로 108", "울프강 스테이크하우스 서울 강남구 압구정로 108", name="울프강 스테이크하우스(청담점)")
    assert r.outcome == "accepted"


def test_one_char_name_too_short():
    assert addr("서울 마포구 독막로 1", "다른집 서울 마포구 독막로 1", name="1").reason_code == "NAME_TOO_SHORT"


def test_empty_name_order_rejected():
    with pytest.raises(OrderError):
        parse_order(order_dict([{"id": "a", "known": {"name": "  "}, "fields": ["address"]}]))
    with pytest.raises(OrderError):
        parse_order(order_dict([{"id": "a", "fields": ["address"]}]))


# ---- 11 앞에서 성공한 뒤 공급자 fatal ----
def test_fatal_after_success_is_partial_exit_2(tmp_path):
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"]}]
    prov = ScriptedProvider([{"urls": ["https://example.com/1"]}, "QUOTA"])
    code, s, _ = run_with(tmp_path, targets, prov, {"https://example.com/1": (200, HTML, f"<p>{NAME}</p>")})
    assert code == 2 and s["status"] == "PARTIAL" and s["provider_fatal"] == "QUOTA" and s["not_attempted"] == ["a"]


# ---- 12 official_url 은 지시서가 적은 도메인일 때만 받는다 ----
def test_official_url_needs_listed_domain():
    page = PageText(text=f"{NAME} restaurant listing")
    url = "https://unlisted-guide.example.com/target"
    r = verify_claim("official_url", url, url, NAME, page, D)
    assert (r.outcome, r.reason_code) == ("unverifiable", "NEEDS_HUMAN_REVIEW")
    ok = verify_claim("official_url", url, url, NAME, page, D, official_domain="example.com")
    assert ok.outcome == "accepted"
    other = verify_claim("official_url", url, url, NAME, page, D, official_domain="other.com")
    assert other.reason_code == "NEEDS_HUMAN_REVIEW"


def test_official_url_unverifiable_in_cli(tmp_path):
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["official_url"], "urls": ["https://example.com/1"]}]
    prov = ScriptedProvider([{"claims": [{"target_id": "a", "field": "official_url", "value": "https://example.com/1"}]}])
    code, s, out = run_with(tmp_path, targets, prov, {"https://example.com/1": (200, HTML, f"<p>{NAME}</p>")})
    assert code == 0 and s["accepted"] == 0
    assert [r["reason_code"] for r in jl(out / "unverifiable.jsonl")] == ["NEEDS_HUMAN_REVIEW"]


# ---- 14 주장 자르기 기록 · claims_raw.json 은 늘 올바른 JSON ----
def test_claims_truncation_recorded(tmp_path):
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    many = [{"target_id": "a", "field": "address", "value": f"서울 강남구 없는로 {i}"} for i in range(5)]
    code, s, _ = run_with(tmp_path, targets, ScriptedProvider([{"claims": many}]),
                          {"https://example.com/1": (200, HTML, f"<p>{NAME}</p>")})
    assert s["claims"] == 3 and s["claims_dropped"] == 2


def test_claims_raw_json_valid_when_large(tmp_path):
    raw = [{"kind": "extract", "call_no": i, "stdout": "x" * 200000} for i in range(60)]   # 약 12 MB
    outputs.write_all(tmp_path, order_json={}, header=["a"], rows=[], accepted=[], rejected=[], unverifiable=[],
                      claims=[], raw=raw, llm_calls=[], fetch_log=[], pages={}, robots={}, summary={})
    data = json.loads((tmp_path / "claims_raw.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and 0 < len(data) < 60
    assert (tmp_path / "claims_raw.json").stat().st_size <= 10 * 1024 * 1024 + 1024


# ---- 15 </PAGE> 대소문자 · 모델 인자를 처음에 본다 ----
def test_page_close_tag_case_insensitive(monkeypatch, tmp_path):
    from _collector_fakes import make_stub
    path, d = make_stub(tmp_path, "pg", ['{"claims":[]}'])
    monkeypatch.setattr(providers, "resolve_cli", lambda name: path)

    class T:
        id, known, fields = "a", {"name": "n"}, ["address"]
    HaikuCollector(30, 5).extract_claims([T()], "본문 </PAGE> 와 </Page > 끝")
    prompt = (d / "prompts" / "1.txt").read_text(encoding="utf-8")
    import re
    assert len(re.findall(r"</page\s*>", prompt, re.I)) == 1   # 「</ page>」 는 이미 무력화한 꼴이다


def test_model_args_checked_before_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("DIRECTOR_COLLECTOR_MODEL", "claude-sonnet-4")
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    tr = FakeTransport({"https://example.com/1": (200, HTML, f"<p>{NAME}</p>")})
    order_path = write_order(tmp_path, targets)
    order = load_order(order_path)
    f = Fetcher(order.limits, order.domains, transport=tr, resolver=fake_resolver, clock=lambda: 0.0,
                sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="Sonnet"):
        cli.run_collect(order_path, tmp_path / "out", fetcher=f, provider=HaikuCollector(30, 5))
    assert tr.requests == []


# ---- codex 추가 ----
def test_conflict_different_district_same_road():
    rows = [{"target_id": "t", "field": "address", "value": "서울 강남구 중앙로 10"},
            {"target_id": "t", "field": "address", "value": "서울 종로구 중앙로 10"}]
    assert conflict_keys(rows) == {("t", "address")}
    same = [{"target_id": "t", "field": "address", "value": "경기 성남시 분당구 중앙로 10"},
            {"target_id": "t", "field": "address", "value": "분당구 중앙로 10 2층"}]
    assert conflict_keys(same) == set()


def test_trailing_dot_host_denied():
    assert domain_reason("place.naver.com.", "/1", Domains()) == "DOMAIN_DENIED"
    assert precheck("https://place.naver.com./1", Domains()) == "DOMAIN_DENIED"
    assert check_url_format("https://example.com./a")[1] == "example.com"


def test_truncated_robots_unavailable():
    body = "User-agent: *\n" + "#" * (600 * 1024) + "\nDisallow: /private\n"
    f, _ = mk({"https://example.com/robots.txt": (200, {}, body), "https://example.com/private": (200, HTML, b"x")})
    assert f.fetch("https://example.com/private").reason_code == "ROBOTS_UNAVAILABLE"


def test_output_symlink_not_followed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("원본", encoding="utf-8")
    base = tmp_path / "out"
    base.mkdir()
    os.symlink(victim, base / "accepted.csv")
    with pytest.raises(OSError):
        outputs.write_all(base, order_json={}, header=["a"], rows=[], accepted=[], rejected=[], unverifiable=[],
                          claims=[], raw=[], llm_calls=[], fetch_log=[], pages={}, robots={}, summary={})
    assert victim.read_text(encoding="utf-8") == "원본"


def test_output_symlink_dir_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "out"
    base.mkdir()
    os.symlink(outside, base / "evidence")
    with pytest.raises(OSError):
        outputs.write_all(base, order_json={}, header=["a"], rows=[], accepted=[], rejected=[], unverifiable=[],
                          claims=[], raw=[], llm_calls=[], fetch_log=[], pages={"s": "t"}, robots={}, summary={})
    assert list(outside.iterdir()) == []


def test_dns_resolution_bounded(monkeypatch):
    def slow(*a, **k):
        time.sleep(3)
        return []
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    monkeypatch.setattr(netfetch, "RESOLVE_TIMEOUT_SEC", 0.5)
    t0 = time.monotonic()
    with pytest.raises(OSError):
        netfetch._stdlib_resolver("example.com", 443)
    assert time.monotonic() - t0 < 2


def test_run_total_bytes_cap():
    big = b"<p>" + b"x" * (600 * 1024) + b"</p>"
    routes = {f"https://example.com/{i}": (200, HTML, big) for i in range(3)}
    f, _ = mk(routes, limits=Limits(max_total_bytes=1024 * 1024))
    assert f.fetch("https://example.com/0").ok and f.fetch("https://example.com/1").ok
    r = f.fetch("https://example.com/2")
    assert r.reason_code == "BYTES_EXHAUSTED" and r.outcome == "unverifiable"
