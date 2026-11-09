import pytest

from core.collector.order import OrderError, parse_order


def base(**over):
    o = {"version": 1, "order_id": "t-1", "purpose": "스테이크 식당 주소",
         "targets": [{"id": "a", "known": {"name": "구스테이크528"}, "fields": ["address"]}],
         "output": {"schema": "steak-sources-v1", "path": "data/sources/web.csv"},
         "requested_at_kst": "2026-11-09T15:00:00+09:00"}
    o.update(over)
    return o


def test_defaults():
    o = parse_order(base())
    assert o.limits.max_fetch_requests == 200 and o.limits.min_interval_sec_per_host == 3
    assert "Chrome" in o.limits.user_agent and o.providers.primary == "claude-haiku"
    assert "siksinhot.com/P/" not in o.domains.deny and "bluer.co.kr" in o.domains.deny and o.providers.report_health_db is False


@pytest.mark.parametrize("over", [
    {"output": {"schema": "steak-sources-v1", "path": "../x.csv"}},
    {"output": {"schema": "steak-sources-v1", "path": "/abs.csv"}},
    {"targets": [{"id": "a", "fields": ["phone"]}]},
    {"limits": {"max_targets": 500}},
    {"providers": {"primary": "agy-flash"}},
    {"requested_at_kst": "2026-11-09T06:00:00Z"},
    {"targets": [{"id": "a", "fields": ["address"], "urls": ["https://a/1", "https://a/2", "https://a/3", "https://a/4"]}]},
    {"targets": [{"id": "a", "fields": ["address"]}, {"id": "a", "fields": ["address"]}]},
    {"targets": [{"id": "a", "fields": ["address"], "known": {"name": "x\x07"}}]},
    {"version": 2},
])
def test_rejected(over):
    with pytest.raises(OrderError):
        parse_order(base(**over))


def test_owner_policy_purpose():
    with pytest.raises(OrderError, match="ORDER_POLICY"):
        parse_order(base(purpose="카드 결제 정보를 모은다 — 실제 결제 진행"))
