"""3차 리뷰(대상 3fab4f2..035ea80) 지적 10건 — 원본에서 재현한 것을 시험으로 고정한다(설계 §13.1)."""
import pytest

from test_collector_review2 import NAME, D, ScriptedProvider, addr, jl, run_with, status, write_order
from core.collector import cli
from core.collector.netfetch import precheck
from core.collector.order import Domains, OrderError
from core.collector.outputs import region_of
from core.collector.verify import _decode, check_official_url, conflict_keys


# ---- 1 폐업 판정 — 낱말이 끼어든 마감 시각 · 부정 ----
@pytest.mark.parametrize("text", [
    f"{NAME} 영업 종료 시간 22:00",
    f"{NAME} 영업 종료 오후 10시",
    f"{NAME} 폐업 안 했습니다",
    f"{NAME} 폐업했다는 소문은 사실이 아닙니다",
    f"{NAME} 폐업설은 사실무근",
    f"{NAME} isn't permanently closed",
])
def test_closed_negation_and_hours_rejected(text):
    assert status(text).outcome == "rejected"


# ---- 2 줄바꿈이 문장 끝이다 · 날짜는 마감 시각이 아니다 ----
@pytest.mark.parametrize("text", [
    f"{NAME}\n폐업\n02-545-0528",
    f"{NAME} 폐업 2024.03.31",
    f"{NAME} 영업 종료: 2024-03-31",
    f"{NAME} 폐업\n1층 상가 임대",
])
def test_closed_with_date_or_newline_accepted(text):
    assert status(text).outcome == "accepted"


# ---- 3 경로 규칙은 퍼센트 인코딩과 무관하게 맞는다 ----
def test_path_rule_non_ascii_deny_and_allow():
    assert precheck("https://example.com/카페/1", Domains(deny=["example.com/카페"])) == "DOMAIN_DENIED"
    assert precheck("https://ko.wikipedia.org/wiki/울프강", Domains(allow=["ko.wikipedia.org/wiki/울프강"])) is None
    assert precheck("https://example.com/%EC%B9%B4%ED%8E%98/1", Domains(deny=["example.com/카페"])) == "DOMAIN_DENIED"


# ---- 4 건물번호 뒤 「번길 · 길 · 가」는 다른 주소다 ----
@pytest.mark.parametrize("value,page", [
    ("서울 강남구 압구정로 108", f"{NAME} 서울 강남구 압구정로 108번길 5"),
    ("서울 압구정로 10", f"{NAME} 서울 압구정로 10길 5"),
    ("서울 중구 을지로 3", f"{NAME} 서울 중구 을지로 3가 10"),
])
def test_building_number_not_prefix_of_other_road(value, page):
    assert addr(value, page).outcome == "rejected"


def test_building_number_followed_by_space_still_matches():
    assert addr("서울 강남구 압구정로 108", f"{NAME} 서울 강남구 압구정로 108 2층").outcome == "accepted"


# ---- 5 시도는 구가 아니다 · 시가 같아도 구가 다르면 다른 곳 ----
def _addr_rows(*vals):
    return [{"target_id": "t", "field": "address", "value": v} for v in vals]


def test_conflict_daegu_is_not_district():
    assert conflict_keys(_addr_rows("대구 달서구 중앙로 10", "대구 수성구 중앙로 10")) == {("t", "address")}


def test_conflict_same_si_different_gu():
    assert conflict_keys(_addr_rows("경기 성남시 분당구 중앙로 10", "경기 성남시 수정구 중앙로 10")) == {("t", "address")}


def test_no_conflict_when_one_side_lacks_gu():
    assert conflict_keys(_addr_rows("경기 성남시 분당구 중앙로 10", "성남시 중앙로 10 2층")) == set()


# ---- 6 URL 전부가 뜻밖의 오류면 정상(0)이 아니다 ----
class _Boom:
    log, robots_texts, requests_made = [], {}, 0

    def fetch(self, url):
        raise RuntimeError("뜻밖의 오류")


def test_all_internal_error_is_not_ok(tmp_path):
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    code, s, out = run_with(tmp_path, targets, ScriptedProvider([]), {}, fetcher=_Boom())
    assert code == 2
    assert s["status"] == "INTERNAL_FAILURE"
    assert s["internal_errors"] == 1
    assert [r["reason_code"] for r in jl(out / "unverifiable.jsonl")] == ["INTERNAL_ERROR"]


# ---- 7 출력 폴더 심볼릭 링크는 웹 · LLM 에 닿기 전에 막는다 ----
def test_symlink_output_rejected_before_fetch(tmp_path):
    targets = [{"id": "a", "known": {"name": NAME}, "fields": ["address"], "urls": ["https://example.com/1"]}]
    order_path = write_order(tmp_path, targets)
    out = tmp_path / "out"
    out.mkdir()
    (out / "other").mkdir()
    (out / "o1").symlink_to(out / "other")   # out 안을 가리키면 resolve 검사를 지나간다

    class NoFetch(_Boom):
        def fetch(self, url):
            raise AssertionError("심볼릭 링크인데 웹에 닿았다")
    with pytest.raises(OrderError):
        cli.run_collect(order_path, out, fetcher=NoFetch(), provider=ScriptedProvider([]))


# ---- 8 「제주도」 · 「세종시」도 시도다 ----
def test_region_jeju_do_and_sejong_si():
    assert region_of("제주도 제주시 연동로 1", "") == "제주"
    assert region_of("세종시 한누리대로 1", "") == "세종"


# ---- 9 charset 은 글자 인코딩만 믿는다 ----
def test_decode_header_idna_does_not_raise():
    assert _decode("가".encode(), "text/html; charset=idna") == "가"


@pytest.mark.parametrize("cs", ["unicode_escape", "utf-16", "rot13"])
def test_decode_meta_non_text_charset_ignored(cs):
    body = f'<meta charset="{cs}">\\uac00 가'.encode()
    assert _decode(body, "text/html") == f'<meta charset="{cs}">\\uac00 가'


# ---- 10 공용 접미사는 공식 도메인이 아니다 ----
@pytest.mark.parametrize("od", ["co.kr", "kr", "com", "or.kr", "seoul.kr", "co.uk"])
def test_public_suffix_official_domain_not_trusted(od):
    r = check_official_url("https://evil.co.kr/", "https://evil.co.kr/", NAME, NAME, D, od)
    assert r == "NEEDS_HUMAN_REVIEW"


def test_real_official_domain_still_accepted():
    assert check_official_url("https://www.gu528.co.kr/", "https://www.gu528.co.kr/", NAME, NAME, D,
                              "gu528.co.kr") is None
