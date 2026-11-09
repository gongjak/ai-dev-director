"""리뷰 지적(2026-11-09) 회귀 시험 — 고치기 전에 실패하는 것을 먼저 확인했다."""
import json
import socket
import threading
import time

import pytest

from _collector_fakes import HTML, FakeTransport, fake_resolver
from core.collector import cli
from core.collector.netfetch import Fetcher, StdlibTransport, TransportError, is_public_ip
from core.collector.order import Domains, Limits, OrderError, parse_order
from core.collector.verify import PageText, conflict_keys, verify_claim

D = Domains()


def addr(value, text, name="구스테이크528"):
    return verify_claim("address", value, "https://example.com/a", name, PageText(text=text), D)


# ---- 1 order_id ----
@pytest.mark.parametrize("oid", [".", "..", ".hidden"])
def test_order_id_dot_rejected(oid):
    o = {"version": 1, "order_id": oid, "purpose": "p", "targets": [{"id": "a", "fields": ["address"]}],
         "output": {"schema": "steak-sources-v1", "path": "x.csv"}, "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    with pytest.raises(OrderError):
        parse_order(o)


def test_cli_out_dir_containment(tmp_path, monkeypatch):
    # 지시서 검증을 지나쳐도 out 밖에 쓰지 않는다
    from core.collector import order as order_mod
    real = order_mod.load_order

    def fake_load(p):
        o = real(p)
        object.__setattr__(o, "order_id", "..")
        return o
    monkeypatch.setattr(cli, "load_order", fake_load)
    o = {"version": 1, "order_id": "ok", "purpose": "p",
         "targets": [{"id": "a", "known": {"name": "구스테이크528"}, "fields": ["address"], "urls": []}],
         "output": {"schema": "steak-sources-v1", "path": "x.csv"}, "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    p = tmp_path / "o.json"
    p.write_text(json.dumps(o), encoding="utf-8")
    with pytest.raises(OrderError):
        cli.run_collect(p, tmp_path / "out" / "inner", fetcher=object(), provider=object())


# ---- 2 주소 경계 · 모호한 값 ----
def test_address_digit_boundary():
    r = addr("서울 용산구 회나무로4", "비스테까 서울 용산구 회나무로 49")
    assert r.outcome == "rejected" and r.reason_code == "VALUE_NOT_ON_PAGE"


def test_address_too_vague():
    r = addr("서울", "구스테이크528 서울 강남구 압구정로 108")
    assert r.reason_code == "ADDRESS_TOO_VAGUE"
    assert addr("서울 강남구", "구스테이크528 서울 강남구 압구정로 108").reason_code == "ADDRESS_TOO_VAGUE"


def test_address_jibun_ok():
    assert addr("서울 용산구 이태원동 211-33", "비스테까 서울 용산구 이태원동 211-33", name="비스테까").outcome == "accepted"


# ---- 3 이름이 값 가까이 ----
def test_address_name_must_be_near():
    text = "구스테이크528 서울 강남구 압구정로 108\n" + "가" * 500 + "\n다른집 서울 마포구 독막로 1"
    r = addr("서울 마포구 독막로 1", text)
    assert r.outcome == "rejected" and r.reason_code == "NAME_NOT_NEAR_VALUE"


def test_address_name_spacing_variant():
    assert addr("서울 강남구 압구정로 108", "구스테이크 528 · 서울 강남구 압구정로 108").outcome == "accepted"
    assert addr("서울 강남구 압구정로 108", "Wolfgang's Steak! 서울 강남구 압구정로 108",
                name="wolfgangs steak").outcome == "accepted"


# ---- 4 폐업 낱말도 이름 가까이 ----
def test_closed_word_must_be_near_name():
    page = PageText(text="구스테이크528 영업 중\n" + "가" * 500 + "\n다른집은 폐업 했습니다")
    r = verify_claim("status", "closed", "https://example.com/a", "구스테이크528", page, D)
    assert r.outcome == "rejected"
    near = PageText(text="구스테이크528 은 폐업 했습니다")
    assert verify_claim("status", "closed", "https://example.com/a", "구스테이크528", near, D).outcome == "accepted"


# ---- 7 시도가 다른 같은 도로명 ----
def test_conflict_different_sido_same_road():
    rows = [{"target_id": "t", "field": "address", "value": "서울 중구 중앙로 10"},
            {"target_id": "t", "field": "address", "value": "부산광역시 중구 중앙로 10"}]
    assert conflict_keys(rows) == {("t", "address")}
    same = [{"target_id": "t", "field": "address", "value": "서울 중구 중앙로 10"},
            {"target_id": "t", "field": "address", "value": "중구 중앙로 10 2층"}]
    assert conflict_keys(same) == set()


# ---- netfetch ----
def mk(routes):
    return Fetcher(Limits(), Domains(), transport=FakeTransport(routes), resolver=fake_resolver,
                   clock=lambda: 0.0, sleep=lambda s: None)


def test_robots_redirect_followed():
    f = mk({"https://example.com/robots.txt": (301, {"Location": "https://www.example.com/robots.txt"}, b""),
            "https://www.example.com/robots.txt": (200, {}, "User-agent: *\nDisallow: /P/\n"),
            "https://example.com/ok": (200, HTML, b"ok")})
    assert f.fetch("https://example.com/P/1").reason_code == "ROBOTS_DISALLOW"
    assert f.fetch("https://example.com/ok").ok


def test_robots_redirect_to_private_blocked():
    # https → https 로 보내야 내림 검사보다 SSRF 검사가 먼저 돈다(2차 리뷰 13)
    f = mk({"https://example.com/robots.txt": (302, {"Location": "https://evil.test/robots.txt"}, b"")})
    assert f.fetch("https://example.com/a").reason_code == "SSRF_BLOCKED"


def test_robots_redirect_loop_unavailable():
    f = mk({"https://example.com/robots.txt": (301, {"Location": "/robots.txt"}, b"")})
    assert f.fetch("https://example.com/a").reason_code == "ROBOTS_UNAVAILABLE"


def test_redirect_downgrade_rejected():
    f = mk({"https://example.com/a": (302, {"Location": "http://example.com/b"}, b""),
            "http://example.com/b": (200, HTML, b"ok")})
    r = f.fetch("https://example.com/a")
    assert r.reason_code == "REDIRECT_DOWNGRADE" and r.outcome == "unverifiable"


@pytest.mark.parametrize("ip,ok", [("64:ff9b::7f00:1", False), ("64:ff9b::a00:5", False),
                                   ("2002:7f00:1::", False), ("2002:c0a8:101::1", False),
                                   ("64:ff9b::5db8:d822", True)])
def test_is_public_ip_embedded_v4(ip, ok):
    assert is_public_ip(ip) is ok


def test_slow_drip_deadline():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        for _ in range(60):
            if stop.is_set():
                break
            try:
                c.sendall(b"x")
            except OSError:
                break
            time.sleep(0.1)
        c.close()
    th = threading.Thread(target=serve, daemon=True)
    th.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(TransportError):
            StdlibTransport().request(scheme="http", host="localhost", port=port, ip="127.0.0.1", path="/",
                                      headers={}, timeout=1.0, max_bytes=10 ** 6)
        assert time.monotonic() - t0 < 3
    finally:
        stop.set()
        srv.close()
