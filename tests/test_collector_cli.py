"""끝에서 끝 — stub haiku + FakeTransport. 지어낸 값이 거부되는지를 함께 본다."""
import csv
import json
import sqlite3

from _collector_fakes import HTML, FakeTransport, fake_resolver, make_stub
from core.collector import cli, outputs, providers
from core.collector.netfetch import Fetcher
from core.collector.order import load_order
from core.collector.providers import HaikuCollector
from scripts.make_collect_order import build_order

STEAK_HEADER_LINE = "name,address,region,category,source_name,source_url,checked_at_kst,status,lat,lng,coord_source"
PAGE = ("<html><head><title>매거진 2770</title></head><body><h3>구스테이크528</h3><p>서울특별시 강남구 압구정로 108</p>"
        "<h3>다른집</h3><p>서울 마포구 독막로 1</p></body></html>")


def write_order(tmp_path, targets, **over):
    o = {"version": 1, "order_id": "o1", "purpose": "식당 주소", "targets": targets,
         "output": {"schema": "steak-sources-v1", "path": "data/sources/web.csv"},
         "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    o.update(over)
    p = tmp_path / "order.json"
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    return p


def run(tmp_path, monkeypatch, targets, responses, routes, **over):
    path, d = make_stub(tmp_path, "cli", responses)
    monkeypatch.setattr(providers, "resolve_cli", lambda name: path)
    order_path = write_order(tmp_path, targets, **over)
    order = load_order(order_path)
    f = Fetcher(order.limits, order.domains, transport=FakeTransport(routes), resolver=fake_resolver,
                clock=lambda: 0.0, sleep=lambda s: None)
    code, summary = cli.run_collect(order_path, tmp_path / "out", fetcher=f,
                                    provider=HaikuCollector(30, order.limits.max_llm_calls))
    return code, summary, tmp_path / "out" / "o1", d


def jl(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_end_to_end_accept_and_fabricated_reject(tmp_path, monkeypatch):
    targets = [{"id": "a", "known": {"name": "구스테이크528", "sido": "서울"}, "fields": ["address"],
                "urls": ["https://example.com/m/2770"]},
               {"id": "b", "known": {"name": "무량원식당"}, "fields": ["address"], "urls": ["https://example.com/m/2770"]}]
    resp = {"claims": [{"target_id": "a", "field": "address", "value": "서울특별시 강남구 압구정로 108", "quote": "q"},
                       {"target_id": "b", "field": "address", "value": "섬진강대로 2770", "quote": "q"},
                       {"target_id": "zzz", "field": "address", "value": "x"}]}
    code, s, out, d = run(tmp_path, monkeypatch, targets, [resp], {"https://example.com/m/2770": (200, HTML, PAGE)})
    assert code == 0 and s["accepted"] == 1 and s["llm_calls"] == 1    # 같은 URL 은 한 번만 뽑는다
    rows = list(csv.DictReader((out / "accepted.csv").open(encoding="utf-8")))
    assert (out / "accepted.csv").read_text(encoding="utf-8").splitlines()[0] == STEAK_HEADER_LINE
    assert rows[0]["address"] == "서울특별시 강남구 압구정로 108" and rows[0]["region"] == "서울"
    assert rows[0]["lat"] == rows[0]["lng"] == rows[0]["coord_source"] == "" and rows[0]["status"] == "unknown"
    assert rows[0]["source_url"] == "https://example.com/m/2770" and len(rows[0]["checked_at_kst"]) == 10
    rej = {r["target_id"]: r["reason_code"] for r in jl(out / "rejected.jsonl")}
    assert rej == {"b": "VALUE_NOT_ON_PAGE", "zzz": "UNKNOWN_TARGET"}
    assert s["rejected"] == len(jl(out / "rejected.jsonl")) and s["unverifiable"] == len(jl(out / "unverifiable.jsonl"))
    for name in ("claims_raw.json", "claims.jsonl", "llm_calls.jsonl", "evidence/fetch_log.jsonl", "summary.json",
                 "collect_order.json"):
        assert (out / name).exists(), name
    assert list((out / "evidence" / "pages").glob("*.txt"))
    claims = jl(out / "claims.jsonl")
    assert all(c["source_url"] == "https://example.com/m/2770" for c in claims)   # URL 은 코드가 붙인다


def test_discovery_then_ssrf_and_denied_urls(tmp_path, monkeypatch):
    targets = [{"id": "a", "known": {"name": "구스테이크528"}, "fields": ["address"]}]
    disc = {"urls": ["http://evil.test/x", "https://place.naver.com/1", "https://example.com/m/2770"]}
    ext = {"claims": [{"target_id": "a", "field": "address", "value": "서울 강남구 압구정로108"}]}
    code, s, out, d = run(tmp_path, monkeypatch, targets, [disc, ext],
                          {"https://example.com/m/2770": (200, HTML, PAGE)})
    assert code == 0 and s["accepted"] == 1
    codes = sorted(r["reason_code"] for r in jl(out / "rejected.jsonl"))
    assert codes == ["DOMAIN_DENIED", "SSRF_BLOCKED"]
    a1 = (d / "args_1.txt").read_text().splitlines()
    assert a1[a1.index("--tools") + 1] == "WebSearch"


def test_blocked_page_is_unverifiable_and_no_llm_call(tmp_path, monkeypatch):
    targets = [{"id": "a", "known": {"name": "n"}, "fields": ["address", "official_url"],
                "urls": ["https://example.com/p"]}]
    code, s, out, _ = run(tmp_path, monkeypatch, targets, [], {"https://example.com/p": (403, HTML, b"")})
    assert code == 0 and s["llm_calls"] == 0 and s["accepted"] == 0
    assert s["reason_codes"] == {"HTTP_BLOCKED": 2}


def test_not_extracted_and_conflict(tmp_path, monkeypatch):
    page2 = "<p>구스테이크528 서울 마포구 독막로 1</p>"
    targets = [{"id": "a", "known": {"name": "구스테이크528"}, "fields": ["address", "status"],
                "urls": ["https://example.com/1", "https://example.com/2"]}]
    r1 = {"claims": [{"target_id": "a", "field": "address", "value": "서울 강남구 압구정로 108"}]}
    r2 = {"claims": [{"target_id": "a", "field": "address", "value": "서울 마포구 독막로 1"}]}
    code, s, out, _ = run(tmp_path, monkeypatch, targets, [r1, r2],
                          {"https://example.com/1": (200, HTML, PAGE), "https://example.com/2": (200, HTML, page2)})
    assert s["accepted"] == 0 and s["reason_codes"] == {"CONFLICT": 2, "NOT_EXTRACTED": 1}


def test_provider_failure_exit_2(tmp_path, monkeypatch):
    targets = [{"id": "a", "known": {"name": "n"}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    code, s, _, _ = run(tmp_path, monkeypatch, targets, ["", ""], {"https://example.com/1": (200, HTML, PAGE)})
    assert code == 2 and s["status"] == "PROVIDER_FAILURE"


def test_health_db_untouched_by_default(tmp_path, monkeypatch, isolate_provider_db):
    def snap():
        with sqlite3.connect(str(isolate_provider_db)) as c:
            return c.execute("SELECT * FROM provider_health ORDER BY provider").fetchall()
    before = snap()
    targets = [{"id": "a", "known": {"name": "n"}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    run(tmp_path, monkeypatch, targets, ['{"claims":[]}'], {"https://example.com/1": (200, HTML, PAGE)})
    assert snap() == before


def test_main_exit_codes(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 9}', encoding="utf-8")
    assert cli.main([str(bad), "--out", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()


def test_coord_forbidden():
    import pytest
    with pytest.raises(ValueError, match="COORD_FORBIDDEN"):
        outputs.steak_row(name="x", lat="37.5")


def test_make_collect_order(tmp_path):
    pend = tmp_path / "pending"
    pend.mkdir()
    (pend / "review.csv").write_text("entry,area,sido,name_part,why,count,candidates\n이태원 GOO STK 733,이태원,서울,GOO STK 733,w,1,c\n",
                                     encoding="utf-8")
    (pend / "notfound.csv").write_text("entry,area,sido,name_part,why\n가로수길 구스테이크528,가로수길,서울,구스테이크528,w\n",
                                       encoding="utf-8")
    order = build_order(pend, "steak-1", ["address"], 200, "data/sources/web.csv")
    assert [t["id"] for t in order["targets"]] == ["review-001", "notfound-001"]
    p = tmp_path / "o.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    assert load_order(p).targets[1].known["name"] == "구스테이크528"
