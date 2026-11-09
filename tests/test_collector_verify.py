"""검사기를 먼저 시험한다 — 값이 없는 페이지 · 지어낸 값이 거부되는지부터 (설계 §11)."""
from core.collector.order import Domains
from core.collector.verify import PageText, conflict_keys, extract_text, match_address, verify_claim

D = Domains()
PAGE = extract_text("""<html><head><title>구스테이크528</title><script>var a="서울 강남구 가짜로 1";</script></head>
<body><h2>구스테이크528</h2><p>주소: 서울특별시 강남구 압구정로 108, 2층</p><p>전화 02-000-0000</p></body></html>""".encode(),
                    "text/html; charset=utf-8")


def v(field, value, page=PAGE, url="https://example.com/a", name="구스테이크528"):
    return verify_claim(field, value, url, name, page, D)


def test_value_not_on_page_rejected_first():
    r = v("address", "서울 강남구 도산대로 1")
    assert (r.outcome, r.reason_code) == ("rejected", "VALUE_NOT_ON_PAGE")


def test_fabricated_value_rejected():
    # 2026-11-09 agy Flash 가 지어낸 답
    r = v("address", "섬진강대로 2770")
    assert r.outcome == "rejected" and r.reason_code == "VALUE_NOT_ON_PAGE"


def test_script_text_is_not_page_text():
    assert v("address", "서울 강남구 가짜로 1").reason_code == "VALUE_NOT_ON_PAGE"


def test_exact_and_sido_abbreviation():
    assert v("address", "서울특별시 강남구 압구정로 108").match_rule == "exact"
    assert v("address", "서울 강남구 압구정로108").outcome == "accepted"


def test_tokens_rule_with_comma_and_floor():
    r = v("address", "서울 강남구 압구정로 108 2층")
    assert r.outcome == "accepted" and r.match_rule in ("exact", "tokens")


def test_tokens_outside_window_rejected():
    page = PageText(text="서울 강남구 " + "가" * 300 + " 압구정로 108")
    assert match_address("서울 강남구 압구정로 108", page.full())[0] is None


def test_zero_width_chars():
    page = PageText(text="구스테이크528 서울 강남구 압구​정로 108")
    assert v("address", "서울 강남구 압구정로 108", page=page).outcome == "accepted"


def test_jsonld_address_only():
    html = '<html><script type="application/ld+json">{"@type":"Restaurant","name":"구스테이크528","address":{"addressRegion":"서울",' \
           '"streetAddress":"강남구 압구정로 108"}}</script><body>없음</body></html>'
    page = extract_text(html.encode(), "text/html")
    assert v("address", "서울 강남구 압구정로 108", page=page).outcome == "accepted"


def test_unsupported_content():
    assert extract_text(b"%PDF", "application/pdf") is None


def test_official_url_rules():
    # 블랙리스트에 없다는 것은 공식이라는 증거가 아니다 — 지시서가 적은 도메인일 때만 받는다(2차 리뷰 12)
    assert v("official_url", "https://example.com/a").reason_code == "NEEDS_HUMAN_REVIEW"
    assert verify_claim("official_url", "https://example.com/a", "https://example.com/a", "구스테이크528", PAGE, D,
                        official_domain="example.com").outcome == "accepted"
    assert v("official_url", "https://blog.naver.com/x", url="https://blog.naver.com/x").reason_code == "NOT_OFFICIAL_HOST"
    assert v("official_url", "https://example.com/a", name="다른식당").reason_code == "NAME_NOT_ON_PAGE"
    assert v("official_url", "https://other.com/").reason_code == "URL_NOT_SOURCE"


def test_status_only_closed():
    assert v("status", "open").reason_code == "STATUS_NOT_CLOSED"
    assert v("status", "closed").reason_code == "VALUE_NOT_ON_PAGE"
    closed = PageText(text="구스테이크528 이 매장은 폐업 했습니다")
    assert v("status", "closed", page=closed).outcome == "accepted"


def test_no_value():
    assert v("address", None).reason_code == "NO_VALUE"


def test_conflict():
    rows = [{"target_id": "a", "field": "address", "value": "서울 강남구 압구정로 108"},
            {"target_id": "a", "field": "address", "value": "서울특별시 강남구 압구정로108"},
            {"target_id": "b", "field": "address", "value": "서울 a로 1"},
            {"target_id": "b", "field": "address", "value": "서울 b로 2"}]
    assert conflict_keys(rows) == {("b", "address")}


def test_conflict_same_place_different_notation_is_not_conflict():
    # 층 표기 · 지번 병기는 같은 곳이다 (2026-11-09 실측: 비스테까 세 출처가 전부 CONFLICT 로 빠졌다)
    from core.collector.verify import conflict_keys
    rows = [{"target_id": "t", "field": "address", "value": "서울특별시 용산구 회나무로 49"},
            {"target_id": "t", "field": "address", "value": "서울 용산구 회나무로 49 1층"},
            {"target_id": "t", "field": "address", "value": "서울 용산구 이태원동 211-33"}]
    assert conflict_keys(rows) == set()


def test_conflict_different_road_address_is_conflict():
    from core.collector.verify import conflict_keys
    rows = [{"target_id": "t", "field": "address", "value": "서울 강남구 압구정로 108"},
            {"target_id": "t", "field": "address", "value": "서울 강남구 선릉로152길 21"}]
    assert conflict_keys(rows) == {("t", "address")}


def test_english_address_rejected():
    from core.collector.verify import verify_claim
    v = verify_claim("address", "21 Seolleung-ro 152-gil, Gangnam-gu, Seoul", "https://x.example", "울프강", None, None)
    assert v.outcome == "rejected" and v.reason_code == "NOT_KOREAN_ADDRESS"
