"""실제 연기 시험(opt-in) — 실제 웹 1페이지 · 실제 claude haiku 1회. 기본 회귀는 돌리지 않는다.
.venv/bin/python -m pytest -m integration_cli tests/test_collector_smoke.py -q -s"""
import json

import pytest

from core.collector import cli


@pytest.mark.integration_cli
def test_smoke_siksin_magazine_goo_steak(tmp_path):
    order = {"version": 1, "order_id": "smoke-goo", "purpose": "연기 시험 — 식당 주소 한 건",
             "targets": [{"id": "goo-528", "known": {"name": "구스테이크528", "sido": "서울"}, "fields": ["address"],
                          "urls": ["https://www.siksinhot.com/theme/magazine/2770"]}],
             "output": {"schema": "steak-sources-v1", "path": "data/sources/web-smoke.csv"},
             "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    p = tmp_path / "order.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    code = cli.main([str(p), "--out", str(tmp_path / "out")])
    base = tmp_path / "out" / "smoke-goo"
    summary = json.loads((base / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print((base / "accepted.csv").read_text(encoding="utf-8"))
    for name in ("rejected.jsonl", "unverifiable.jsonl"):
        print(name, (base / name).read_text(encoding="utf-8"))
    accepted = [json.loads(x) for x in (base / "accepted.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert code == 0
    assert any("압구정로 108" in a["value"] or "압구정로108" in a["value"] for a in accepted)
