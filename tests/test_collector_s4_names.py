"""이름 대조(연도 · 회차를 뗀 이름)와 S4 「기관 소개」 규칙 — 2026-11-25 실제 수집(서울 자치구 축제 20곳 · 받음 0)에서 나온 것.

검사기를 먼저 본다 — 구청 첫 화면 · 뉴스(mediahub) · 게시판 글 · 목록 · 지난해 날짜뿐인 소개 쪽이 받아지지 않는지가 첫 시험이다.
네트워크 · 실제 CLI 없이 돈다.
"""
import pytest

from _collector_fakes import HTML
from agents.claude_head import ClaudeHead
from agents.head import BaseHead
from core.collector.order import Domains
from core.collector.verify import (PageText, core_name, decide_official_url, extract_text, name_near, s4_url_problem,
                                   verify_claim, _name_in, _name_whole_in)
from core.collector import stage
from test_collector_official_url import DNS, jl, rows, run, stage_factory, stage_order, tgt

D = Domains()
DDM = "https://www.ddm.go.kr/tour/contents.do?key=1463"
SIBLINGS = ("배봉산 해맞이 축제", "선농제향", "청룡문화제", "한방문화축제")


def page(title="", h1="", body=""):
    doc = f"<html><head><title>{title}</title></head><body><h1>{h1}</h1>{body}</body></html>"
    return extract_text(doc.encode("utf-8"), "text/html; charset=utf-8")


def intro(name="동대문구 봄꽃축제", date="<p>기간 : 2026. 4. 4. ~ 4. 5.</p>", extra=""):
    """구청 문화관광 사이트의 축제 소개 쪽 꼴(실제 ddm.go.kr) — 차림에 이웃 축제가 두 번씩 있고 본문은 그 축제 하나다."""
    nav = "<ul>" + "".join(f"<li><a href='/tour/contents.do?key={i}'>{x}</a></li>"
                           for i, x in enumerate(SIBLINGS + (name,))) + "</ul>"
    body = nav + nav + f"<h2>{name}</h2><p>{name} 이미지</p><p>{name}는 중랑천 둔치에서 열린다.</p>{date}{extra}"
    return page(f"{name} - 문화관광", "문화관광", body)


def decide(url, name, pg, final=None, year="2026", **known):
    return decide_official_url(url, final or url, {"name": name, **known}, pg, D, year, [])


# ------------------------------------------------------------------ 1. 이름 — 연도 · 회차를 떼고 견준다

@pytest.mark.parametrize("name,core", [
    ("2026 강북 백맥축제", "강북 백맥축제"), ("제6회 강서문화예술페스티벌", "강서문화예술페스티벌"),
    ("2026년 마곡문화거리버스킹", "마곡문화거리버스킹"), ("강북 백맥축제 2026", "강북 백맥축제"),
    ("2026 제3회 공예문화축제", "공예문화축제"), ("6회 능골산 문화축제", "능골산 문화축제"),
    ("2026년도 강서봄빛페스타", "강서봄빛페스타"), ("동대문페스티벌", "동대문페스티벌"),
])
def test_core_name_strips_edge_year_and_ordinal(name, core):
    assert core_name(name) == core


@pytest.mark.parametrize("name", ["카페 1984", "2026 축제", "제3회", "구스테이크 528", "을지로3가 식당", "1회용품 없는 축제",
                                  "3회차 정기공연 축제"])
def test_core_name_keeps_names_that_would_become_too_short_or_have_no_edge_token(name):
    """떼고 남는 것이 네 글자 미만이면 떼지 않는다(「카페」 · 「축제」 는 어디에나 있다). 연도 · 회차가 아닌 수는 그대로다."""
    assert core_name(name) == name


@pytest.mark.parametrize("name,text", [
    ("2026 강북 백맥축제", "강북 백맥축제 안내"), ("2026 강북 백맥축제", "강북백맥축제"),
    ("제6회 강서문화예술페스티벌", "제7회 강서문화예술페스티벌 개최"), ("2026년 마곡문화거리버스킹", "마곡 문화거리 버스킹"),
    ("제6회 강서문화예술페스티벌", "제6회강서문화예술페스티벌"), ("2026년 마곡문화거리버스킹", "2025년마곡문화거리버스킹"),
    ("2026 강북 백맥축제", "2026강북백맥축제"),
])
def test_stripped_name_is_found_and_counts_as_a_whole_word(name, text):
    assert _name_in(name, text) and _name_whole_in(name, text)


@pytest.mark.parametrize("name,text", [
    ("장미축제", "서울장미축제"), ("장미축제", "장미축제추진위원회"), ("2026 장미축제", "서울장미축제"),
    ("제3회 공예문화축제", "한지공예문화축제"), ("제3회 공예문화축제", "제5회 한지공예문화축제"),
    ("2026 강북 백맥축제", "강북 백맥축제추진단"), ("제3회 공예문화축제", "2026년 전주한지공예문화축제"),
])
def test_stripped_name_inside_a_longer_name_is_not_a_whole_word(name, text):
    """낱말 경계는 뗀 이름에 그대로 선다 — 앞뒤에 한글이 맞닿으면 다른 이름이다. 맞닿아도 되는 것은 연도 · 회차뿐이다."""
    assert not _name_whole_in(name, text)


def test_stripped_name_reaches_both_official_url_checks_and_nothing_else():
    # 리뷰(fable 3) 뒤 — 주소 · 폐업 대조에는 뗀 이름이 닿지 않는다. official_url 의 두 길에만 닿는다
    pg = PageText(title="강북 백맥축제", text="강북 백맥축제 장소 서울 강북구 도봉로 89길 13")
    v = verify_claim("address", "서울 강북구 도봉로89길 13", "https://a.example.com/x", "2026 강북 백맥축제", pg, D)
    assert v.reason_code == "NAME_NOT_NEAR_VALUE"
    # 옛 경로(값을 밖에서 받았을 때)의 이름 검사도 같다 — 이름이 없으면 NAME_NOT_ON_PAGE, 있으면 그 다음 조건으로 간다
    old = lambda text: verify_claim("official_url", "https://a.example.com", "https://a.example.com/",
                                    "2026 강북 백맥축제", PageText(text=text), D)
    assert old("다른 축제").reason_code == "NAME_NOT_ON_PAGE"
    assert old("강북백맥축제 안내").reason_code == "NEEDS_HUMAN_REVIEW"


def test_decide_no_longer_rejects_a_page_that_omits_the_year():
    """실제 사례 — known.name 「2026 강북 백맥축제」 · 페이지 제목은 「강북 백맥축제」.
    리뷰(fable 2) 뒤 — 뗀 이름으로만 맞았으면 그 해의 날짜가 이름 곁에 있어야 한다."""
    pg = page("강북 백맥축제", "강북 백맥축제", "<p>강북 백맥축제</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    v = decide("https://www.gangbuk.go.kr/", "2026 강북 백맥축제", pg)
    assert (v.outcome, v.match_rule) == ("accepted", "S1")


# ------------------------------------------------------------------ 2. S4 — 검사기 먼저(받으면 안 되는 것)

def test_s4_ward_office_home_page_is_not_s4():
    """구청 첫 화면 — 축제 이름이 본문에 있어도 제목은 구청이다. S4 는 깊은 쪽에만 선다."""
    pg = page("노원구청", "노원구", "<p>노원수제맥주축제 기간 : 2026. 6. 16. ~ 6. 17.</p>")
    v = decide("https://www.nowon.go.kr/", "노원수제맥주축제", pg)
    assert (v.outcome, v.detail, v.match_rule) == ("unverifiable", "NAME_NOT_IN_TITLE", None)


@pytest.mark.parametrize("url", [
    "https://mediahub.seoul.go.kr/archives/2011163", "https://news.seoul.go.kr/culture/archives/5", "https://www.korea.kr/x/1",
])
def test_s4_news_hosts_are_excluded(url):
    v = decide(url, "동대문구 봄꽃축제", intro())
    assert v.outcome != "accepted"


@pytest.mark.parametrize("url", [
    "https://www.ddm.go.kr/www/selectBbsNttView.do?bbsNo=38&nttNo=1", "https://www.ddm.go.kr/board/view.do?id=3",
    "https://www.ddm.go.kr/tour/notice/123", "https://www.ddm.go.kr/tour/festivalList.do",
    "https://www.ddm.go.kr/search/result.do?q=x", "https://www.ddm.go.kr/tour/contents.do?searchKeyword=abc",
    "https://www.ddm.go.kr/news/view/3", "https://www.ddm.go.kr/tour/view.do?boardId=3&key=1",
])
def test_s4_board_list_search_urls_are_excluded(url):
    assert s4_url_problem(url)
    v = decide(url, "동대문구 봄꽃축제", intro())
    assert (v.outcome, v.detail) == ("unverifiable", "NOT_HOME_PAGE")


@pytest.mark.parametrize("url", [
    DDM, "https://festival.seoul.go.kr/festival/main/festivalView.do?festacode=28",
    "https://culture.seoul.go.kr/culture/culture/cultureEvent/view.do?cultcode=146600&menuNo=200008",
    "https://www.mapo.go.kr/site/culture/fes/culture_onstop_fes_view?motSeq=2038",
    "https://www.mcst.go.kr/site/s_culture/festival/festivalView.jsp?pSeq=13484",
    "https://culture.seoul.go.kr/night/sub/program/view.do?pgmId=6950",
])
def test_s4_real_detail_urls_pass_the_url_check(url):
    """2026-11-25 실제 수집에서 본 소개 쪽 URL 들 — 「main」 · 「view」 가 든 경로를 목록으로 읽지 않는다."""
    assert s4_url_problem(url) is None


def test_s4_needs_gov_host_title_name_and_this_years_date():
    name = "동대문구 봄꽃축제"
    assert decide("https://www.ddmac.or.kr/festival/view?id=3", name, intro()).detail == "NOT_HOME_PAGE"      # .go.kr 가 아니다
    assert decide("https://tour.ddm.kr/festival/view?id=3", name, intro()).outcome == "unverifiable"
    no_title = page("문화관광", "문화관광", f"<h2>{name}</h2><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert decide(DDM, name, no_title).outcome == "unverifiable"
    last_year = intro(date="<p>기간 : 2025. 4. 5. ~ 4. 6.</p>")
    assert decide(DDM, name, last_year).outcome == "unverifiable"
    no_date = intro(date="<p>행사시기 매년 4월 초 · 중순 봄꽃 개화기</p>")       # 실제 ddm.go.kr 쪽 — 연도가 아예 없다
    assert decide(DDM, name, no_date).outcome == "unverifiable"
    board_date = intro(date="<p>최종 수정일 2026.11.03</p>")                     # 글 날짜는 축제 날짜가 아니다
    assert decide(DDM, name, board_date).outcome == "unverifiable"
    assert decide(DDM, name, intro(), year="2027").outcome == "unverifiable"   # known.year 가 다르다


def test_s4_list_page_with_other_festivals_is_excluded():
    """제목에 이름이 있어도 다른 축제가 그 이름만큼 나오는 쪽은 목록이다."""
    name = "동대문구 봄꽃축제"
    items = "".join(f"<li><a href='/tour/contents.do?key={i}'>{x}</a> 2026. 4. {i + 1}. ~ 4. {i + 2}.</li>"
                    for i, x in enumerate(SIBLINGS + (name,)))
    pg = page(f"{name} 등 동대문구 축제", "문화관광", f"<ul>{items}</ul><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    v = decide("https://www.ddm.go.kr/tour/contents.do?key=1400", name, pg)
    assert (v.outcome, v.detail) == ("unverifiable", "NOT_HOME_PAGE")


# ------------------------------------------------------------------ 2. S4 — 받는 것

def test_s4_accepts_institution_intro_page_with_the_full_url():
    v = decide(DDM + "&utm_source=x&fbclid=y", "동대문구 봄꽃축제", intro())
    assert (v.outcome, v.match_rule, v.value) == ("accepted", "S4", DDM)        # origin 이 아니라 그 쪽 · 추적 인자는 뗀다
    assert v.quote == "동대문구 봄꽃축제 - 문화관광" and "DATES" in v.signals


def test_s4_works_with_a_stripped_name_and_first_h1():
    # 리뷰(fable 1) 뒤 — 첫 h1 말고 본문에도 이름이 있고 날짜가 그 곁에 있어야 한다
    pg = page("문화포털", "강서봄빛페스타", "<h2>강서봄빛페스타</h2><p>일시 : 2026.04.11 ~ 04.12</p>")
    url = "https://festival.seoul.go.kr/festival/main/festivalView.do?festacode=28"
    v = decide(url, "2026년 강서봄빛페스타", pg)
    assert (v.outcome, v.match_rule, v.value) == ("accepted", "S4", url)


def test_s4_drops_session_id_and_refuses_a_url_that_is_not_plain():
    v = decide("https://www.ddm.go.kr/tour/contents.do;jsessionid=ABC123?key=1463", "동대문구 봄꽃축제", intro())
    assert (v.match_rule, v.value) == ("S4", DDM)
    long = decide("https://www.ddm.go.kr/tour/contents.do?key=" + "1" * 300, "동대문구 봄꽃축제", intro())
    assert long.outcome != "accepted"                                       # 다듬을 수 없으면 origin 으로 받지 않는다


def test_s1_s3_are_unchanged_by_s4():
    home = page("무주반딧불축제", "무주반딧불축제", "x")
    assert decide("https://www.firefly.go.kr/", "무주반딧불축제", home).match_rule == "S1"
    deep_or = decide("https://www.firefly.or.kr/intro/a/b", "무주반딧불축제", page("무주반딧불축제", "", "기간 : 2026.08.29 ~ 09.06"))
    assert (deep_or.outcome, deep_or.detail) == ("unverifiable", "NOT_HOME_PAGE")


# ------------------------------------------------------------------ 2. S4 — 수집 실행

INTRO_HTML = ("<html><head><title>동대문구 봄꽃축제 - 문화관광</title></head><body><h1>문화관광</h1>"
              "<h2>동대문구 봄꽃축제</h2><p>기간 : 2026. 4. 4. ~ 4. 5.</p></body></html>")
FEST_HOME = ("<html><head><title>동대문구 봄꽃축제</title></head><body><h1>동대문구 봄꽃축제</h1>"
             "<p>기간 : 2026. 4. 4. ~ 4. 5.</p></body></html>")


def test_cli_s4_row_carries_the_deep_url_and_rule(tmp_path):
    DNS["www.ddm.go.kr"] = ["93.184.216.50"]
    code, s, out, prov = run(tmp_path, [tgt("t15", "동대문구 봄꽃축제", [DDM])], {DDM: (200, HTML, INTRO_HTML)})
    assert s["accepted"] == 1 and s["llm_calls"] == 0
    assert [(r["target_id"], r["value"], r["match_rule"]) for r in rows(out / "accepted.csv")] == [("t15", DDM, "S4")]


def test_stage_places_the_deep_url_and_shows_the_builder_only_the_host(tmp_path):
    """놓인 파일에는 그 쪽의 전체 URL 이 있다. Builder 프롬프트의 예시 행은 예전 규칙대로 호스트까지만 보인다(경로 · 질의는 남의 글)."""
    DNS["www.ddm.go.kr"] = ["93.184.216.50"]
    wt = tmp_path / "wt"
    wt.mkdir()
    order = stage_order([tgt("t15", "동대문구 봄꽃축제", [DDM])])
    res = stage.run_stage(order, str(wt), tmp_path / "run", factory=stage_factory({DDM: (200, HTML, INTRO_HTML)}))
    assert res["files"] == ["data/homepages.csv"] and res["counts"]["accepted"] == 1
    assert [(r["target_id"], r["value"], r["match_rule"]) for r in rows(wt / "data/homepages.csv")] == [("t15", DDM, "S4")]
    preview = stage.builder_preview(order, wt)["data/homepages.csv"]
    assert preview["total_rows"] == 1 and "https://www.ddm.go.kr/" in preview["sample_rows"][0]
    assert not any("key=1463" in c for c in preview["sample_rows"][0])


def test_cli_s4_yields_to_a_stronger_rule_instead_of_conflicting(tmp_path):
    """축제 자신의 사이트(S1 · S1D · S2 · S3)를 받았으면 기관 소개 쪽은 후보로 물러난다 — 둘이 CONFLICT 로 같이 떨어지지 않는다."""
    DNS["www.ddm.go.kr"] = ["93.184.216.50"]
    DNS["www.bomfesta.or.kr"] = ["93.184.216.51"]
    routes = {DDM: (200, HTML, INTRO_HTML), "https://www.bomfesta.or.kr/": (200, HTML, FEST_HOME)}
    code, s, out, _ = run(tmp_path, [tgt("t15", "동대문구 봄꽃축제", list(routes))], routes)
    assert [(r["value"], r["match_rule"]) for r in jl(out / "accepted.jsonl")] == [("https://www.bomfesta.or.kr", "S1D")]
    assert [(r["value"], r["detail"]) for r in rows(out / "candidates.csv")] == [(DDM, "S4_YIELDED")]
    assert "CONFLICT" not in s["reason_codes"]


def test_cli_two_s4_pages_for_one_target_still_conflict(tmp_path):
    DNS["www.ddm.go.kr"] = ["93.184.216.50"]
    DNS["culture.seoul.go.kr"] = ["93.184.216.52"]
    other = "https://culture.seoul.go.kr/culture/culture/cultureEvent/view.do?cultcode=1"
    routes = {DDM: (200, HTML, INTRO_HTML), other: (200, HTML, INTRO_HTML)}
    code, s, out, _ = run(tmp_path, [tgt("t15", "동대문구 봄꽃축제", list(routes))], routes)
    assert s["accepted"] == 0 and s["reason_codes"] == {"CONFLICT": 2}


# ------------------------------------------------------------------ 3. HEAD 안내

def test_head_prompts_tell_ascii_target_ids_and_a_new_output_file():
    text = BaseHead.ORDER_SCHEMA_TEXT
    assert '"id": "t01"' in text and "ASCII" in text and "known.ref" in text
    assert "must not exist yet" in text and "batch suffix" in text
    for head_cls, method in ((BaseHead, "plan_next"), (ClaudeHead, "chat")):
        import inspect
        src = inspect.getsource(getattr(head_cls, method))
        assert "ORDER_SCHEMA_TEXT" in src and "COLLECT_OUTPUT_EXISTS" in src


def test_order_accepts_known_ref_and_rejects_korean_ids(tmp_path):
    from core.collector.order import OrderError, parse_order
    base = {"version": 1, "order_id": "o1", "purpose": "x", "output": {"schema": "generic-claims-v1", "path": "d/a.csv"},
            "requested_at_kst": "2026-11-25T16:37:09+09:00"}
    ok = parse_order({**base, "targets": [tgt("t01", "강북 백맥축제", [], ref="서울-강북-백맥축제", year="2026")]})
    assert ok.targets[0].known["ref"] == "서울-강북-백맥축제"
    with pytest.raises(OrderError):
        parse_order({**base, "targets": [tgt("서울-강북-백맥축제", "강북 백맥축제", [])]})


# ------------------------------------------------------------------ 4. 리뷰(2026-11-25 · agy Neon high + fable) 뒤

from core.collector import verify as V  # noqa: E402

NAME = "동대문구 봄꽃축제"


def blocker(v):
    return (v.outcome, v.s4_blocker)


@pytest.mark.parametrize("date", [
    "<p>게시기간 2026.04.01 ~ 2026.04.30</p>", "<p>최종수정일시 : 2026.11.03</p>", "<p>게시일시 : 2026.11.03</p>",
    "<p>조회기간 2026.01.01 ~ 2026.12.31</p>", "<p>이달의 일정 2026.11.03</p>",
])
def test_s4_page_dates_that_are_not_the_festivals_are_not_counted(date):
    """fable 1 — 쪽의 게시 · 수정 · 조회 날짜와 달력은 그 축제의 날짜가 아니다."""
    assert decide(DDM, NAME, intro(date=date)).outcome == "unverifiable"


def test_s4_date_must_sit_next_to_the_name_in_the_body():
    """fable 1 — 그 해 날짜가 쪽 어딘가에 있는 것으로는 모자란다. 본문의 그 이름 가까이에 있어야 한다."""
    far = intro(date="<p>행사시기 매년 4월</p>", extra="<h3>청룡문화제</h3><p>기간 : 2026. 5. 1. ~ 5. 3.</p>")
    assert blocker(decide(DDM, NAME, far)) == ("unverifiable", "DATE_NOT_NEAR_NAME")
    filler = intro(date="<p>" + "가나다라마바사 " * 40 + "</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert blocker(decide(DDM, NAME, filler)) == ("unverifiable", "DATE_NOT_NEAR_NAME")
    # 띠 h1 · 제목에만 이름이 있고 본문은 다른 행사다
    banner = page("문화관광", NAME, "<h2>선농제향</h2><p>선농제향은 선농단에서 열린다</p><p>기간 : 2026. 5. 1. ~ 5. 3.</p>")
    assert blocker(decide(DDM, NAME, banner)) == ("unverifiable", "DATE_NOT_NEAR_NAME")
    title_only = page(f"{NAME} - 문화관광", "문화관광", "<p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert blocker(decide(DDM, NAME, title_only)) == ("unverifiable", "DATE_NOT_NEAR_NAME")


@pytest.mark.parametrize("date", [
    "<p>행사기간 : 2026. 4. 4. ~ 4. 5.</p>", "<p>축제기간 2026.04.04</p>", "<p>개최일시 : 2026년 4월 4일</p>",
    "<table><tr><th>일정</th><td>2026. 4. 4.(토) ~ 4. 5.(일)</td></tr></table>", "<p>2026. 4. 4. ~ 4. 5. 중랑천 둔치</p>",
])
def test_s4_festival_date_labels_still_count(date):
    v = decide(DDM, NAME, intro(date=date))
    assert (v.outcome, v.match_rule) == ("accepted", "S4")


def test_title_with_a_different_year_is_not_this_years_page():
    """fable 2 · agy 4 — 이름에서 연도를 뗐다고 지난해 쪽의 제목이 맞는 것은 아니다."""
    old = page("2024 강북 백맥축제", "2024 강북 백맥축제", "<p>강북 백맥축제 기간 : 2026. 4. 4. ~ 4. 5.</p>")
    v = decide("https://www.gangbuk.go.kr/", "2026 강북 백맥축제", old)
    assert (v.outcome, v.detail) == ("unverifiable", "NAME_NOT_IN_TITLE")
    v = decide("https://www.gangbuk.go.kr/tour/contents.do?key=3", "2026 강북 백맥축제", old)
    assert blocker(v) == ("unverifiable", "NAME_NOT_IN_TITLE")
    after = page("강북 백맥축제 2025", "", "<p>강북 백맥축제 기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert decide("https://www.gangbuk.go.kr/", "강북 백맥축제", after).detail == "NAME_NOT_IN_TITLE"
    same = page("2026 강북 백맥축제", "", "<p>강북 백맥축제 기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert decide("https://www.gangbuk.go.kr/", "강북 백맥축제", same).match_rule == "S1"


def test_s1_on_a_stripped_name_needs_this_years_date_next_to_the_name():
    """fable 2 — S1 은 날짜를 보지 않는다. 연도 · 회차를 뗀 이름으로만 맞았으면 그 해의 날짜가 이름 곁에 있어야 한다."""
    url = "https://www.gangbuk.go.kr/"
    bare = page("강북 백맥축제", "강북 백맥축제", "x")
    v = decide(url, "2026 강북 백맥축제", bare)
    assert (v.outcome, v.detail) == ("unverifiable", "STRIPPED_NAME_NEEDS_DATE")
    assert decide(url, "강북 백맥축제", bare).match_rule == "S1"                   # 이름 전체가 맞으면 예전 그대로다
    dated = page("강북 백맥축제", "", "<p>강북 백맥축제</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert decide(url, "2026 강북 백맥축제", dated).match_rule == "S1"
    assert decide(url, "2026 강북 백맥축제", page("2026 강북 백맥축제", "", "x")).match_rule == "S1"


@pytest.mark.parametrize("name,title", [
    ("2026 봄꽃축제", "성북 봄꽃축제"), ("2026 봄꽃축제 (동대문구)", "여의도 봄꽃축제 - 영등포구"),
    ("제3회 가을문화축제", "성북 가을문화축제"), ("2026 어린이날 축제", "노원구 어린이날 축제"),
])
def test_generic_core_name_needs_the_full_name(name, title):
    """fable 2 · agy 2 — 뗀 이름이 보통 낱말뿐이면(「봄꽃축제」) 그 이름으로는 견주지 않는다. 지역까지 든 이름 전체가 있어야 한다."""
    body = f"<p>{title}</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>"
    assert decide("https://www.sb.go.kr/", name, page(title, title, body)).outcome != "accepted"
    assert decide("https://www.sb.go.kr/tour/c.do?key=3", name, page(title, title, body)).outcome != "accepted"
    assert not _name_whole_in(name, title)
    assert _name_whole_in(name, name + " 안내")


def test_s4_page_must_mention_the_known_area():
    """agy 2 — S4 는 전국의 .go.kr 깊은 쪽을 받는다. 지시서가 지역을 적었으면 그 지역이 쪽에 있어야 한다."""
    other = page("한강 불빛야행 - 영등포구", "관광", "<p>한강 불빛야행</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>")
    url = "https://www.ydp.go.kr/tour/contents.do?key=100"
    assert blocker(decide(url, "한강 불빛야행", other, area="서울특별시 마포구")) == ("unverifiable", "AREA_NOT_ON_PAGE")
    assert decide(url, "한강 불빛야행", other, area="서울특별시 영등포구").match_rule == "S4"
    assert decide(url, "한강 불빛야행", other).match_rule == "S4"                 # 지역을 적지 않았으면 보지 않는다


@pytest.mark.parametrize("name,core", [
    ("강남문화제 3회", "강남문화제"), ("수원화성문화제 61회", "수원화성문화제"), ("강북 백맥축제 6회", "강북 백맥축제"),
    ("서울국제영화제 20회", "서울국제영화제"), ("제6회 강서문화예술페스티벌", "강서문화예술페스티벌"),
])
def test_core_name_does_not_eat_the_last_syllable_of_the_name(name, core):
    """agy 1 — 「~제 N회」 의 「제」 는 이름의 끝 글자다."""
    assert core_name(name) == core
    assert not _name_whole_in("강남문화제 3회", "강남문화 - 시설 안내")
    assert _name_whole_in("제6회 강서문화예술페스티벌", "2026년제6회강서문화예술페스티벌")


def test_year_like_number_in_the_name_is_kept_when_it_is_not_the_target_year():
    """agy 4 — 「2030 청년페스티벌」 의 2030 은 개최 연도가 아니다. 대상 연도와 같은 수만 뗀다."""
    assert core_name("2030 청년페스티벌", "2026") == "2030 청년페스티벌"
    assert core_name("2026 강북 백맥축제", "2026") == "강북 백맥축제"
    other = page("청년페스티벌", "청년페스티벌", "<p>청년페스티벌 기간 : 2026. 4. 4. ~ 4. 5.</p>")
    assert decide("https://www.sb.go.kr/", "2030 청년페스티벌", other).outcome != "accepted"


def test_stripped_name_is_only_for_official_url():
    """fable 3 — 연도를 뗀 이름은 주소 · 폐업 대조에 쓰지 않는다(「평양면옥 1985」 ↔ 「평양면옥 논현점」 폐업)."""
    pg = PageText(title="평양면옥", text="평양면옥 논현점 폐업")
    assert verify_claim("status", "closed", "https://a.example.com/x", "평양면옥 1985", pg, D).reason_code == "NAME_NOT_NEAR_VALUE"
    pg = PageText(title="평양면옥", text="평양면옥 논현점 서울 강남구 논현로 150")
    v = verify_claim("address", "서울 강남구 논현로 150", "https://a.example.com/x", "평양면옥 1985", pg, D)
    assert v.reason_code == "NAME_NOT_NEAR_VALUE"
    assert not name_near("제6회 강서문화예술페스티벌", "강서문화예술페스티벌 폐업", 11, 13)


@pytest.mark.parametrize("url,value", [
    ("https://a.go.kr/v.do?nm=%C3%E0%C1%A6&id=1", "https://a.go.kr/v.do?nm=%C3%E0%C1%A6&id=1"),      # EUC-KR — 다시 부호화하지 않는다
    ("https://a.go.kr/v.do?a=1;b=2", "https://a.go.kr/v.do?a=1;b=2"),
    ("https://a.go.kr/v.do?flag&id=1", "https://a.go.kr/v.do?flag&id=1"),
    ("https://a.go.kr/fest/", "https://a.go.kr/fest/"),                                              # 끝 「/」 는 다른 쪽일 수 있다
    ("https://A.go.kr:443/v.do?id=1&JSESSIONID=AB&PHPSESSID=x&utm_source=k&fbclid=z", "https://a.go.kr/v.do?id=1"),
    ("https://a.go.kr/v.do;jsessionid=AB?id=1&_gl=3&msclkid=4", "https://a.go.kr/v.do?id=1"),
])
def test_clean_url_filters_keys_without_rewriting_the_query(url, value):
    """fable 4 — 값은 원래 질의 글에서 열쇠만 걸러 만든다."""
    assert V.clean_url(url) == value


def test_s4_value_keeps_the_query_as_fetched_and_refuses_session_like_keys():
    v = decide("https://www.ddm.go.kr/tour/contents.do?nm=%C3%E0%C1%A6&key=1463", NAME, intro())
    assert (v.match_rule, v.value) == ("S4", "https://www.ddm.go.kr/tour/contents.do?nm=%C3%E0%C1%A6&key=1463")
    v = decide("https://www.ddm.go.kr/tour/festival/", NAME, intro())
    assert (v.match_rule, v.value) == ("S4", "https://www.ddm.go.kr/tour/festival/")
    # 「sid」 · 「token」 은 세션일 수도 쪽 번호일 수도 있다 — 떼면 다른 쪽이 되고 두면 남의 세션이다. 받지 않는다
    for q in ("sid=3", "token=abc", "SID=3&key=1463", "access_token=x"):
        assert blocker(decide(f"https://www.ddm.go.kr/tour/contents.do?{q}", NAME, intro())) == ("unverifiable", "SESSION_QUERY")
    # agy 6 — 질의는 남의 글이다. 길거나 많은 것은 받지 않는다
    assert blocker(decide(DDM + "&note=" + "IGNORE_RULES_" * 4, NAME, intro())) == ("unverifiable", "QUERY_TOO_LONG")
    many = "&".join(f"k{i}={i}" for i in range(8))
    assert blocker(decide(f"https://www.ddm.go.kr/tour/contents.do?{many}", NAME, intro())) == ("unverifiable", "QUERY_TOO_LONG")
    # 견줄 때(모순 판정)는 예전처럼 끝 「/」 를 무시한다
    assert V.normalize_url("https://a.go.kr/fest/") == V.normalize_url("https://a.go.kr/fest")


@pytest.mark.parametrize("url", [
    "https://www.ddm.go.kr/brd/view.do?id=3", "https://www.ddm.go.kr/bodo/3", "https://www.ddm.go.kr/article/33",
    "https://www.ddm.go.kr/tour/view.do?q=x", "https://www.ddm.go.kr/tour/view.do?kwd=x", "https://www.ddm.go.kr/tour/view.do?srchWrd=x",
    "https://www.ddm.go.kr/tour/view.do?id=3&page=2", "https://www.ddm.go.kr/tour/view.do?id=3&pageIndex=2",
    "https://www.ddm.go.kr/tour/contents.do?menu=board&id=3", "https://www.ddm.go.kr/tour/contents.do?mode=list&mid=notice",
    "https://www.ddm.go.kr/%62oard/view.do?id=3", "https://gnews.gg.go.kr/briefing/brief_gongbo_view.do?number=123",
    "https://enews.ddm.go.kr/view/3", "https://newsroom.ddm.go.kr/a/3", "https://pressroom.ddm.go.kr/a/3",
])
def test_s4_more_board_news_and_search_urls_are_excluded(url):
    """fable 5 · agy 5."""
    assert s4_url_problem(url)


def test_s4_list_page_where_the_target_name_is_repeated():
    """agy 3 — 요약 문구에 이름이 한 번 더 나와도 목록은 목록이다."""
    pg = page(f"{NAME} 등 2026 축제 안내", "문화", f"<p>{NAME} 등 동대문구 주요 행사</p><ul><li>배봉산 해맞이 축제 2026. 1. 1.</li>"
              f"<li>청룡문화제 2026. 5. 1.</li><li>한방문화축제 2026. 10. 1.</li><li>{NAME} 2026. 4. 4. ~ 4. 5.</li></ul>")
    assert blocker(decide("https://www.ddm.go.kr/tour/contents.do?key=1400", NAME, pg)) == ("unverifiable", "LIST_PAGE")


def test_s4_list_page_of_events_without_festival_words():
    """fable 6 — 이웃 행사 이름에 「축제」 가 없어도 날짜 달린 줄이 여럿이면 목록이다."""
    pg = page(f"{NAME} - 문화관광", "문화", f"<h2>{NAME}</h2><ul><li>선농제향 2026. 4. 20. ~ 4. 21.</li>"
              f"<li>어린이날 큰잔치 2026. 5. 5. ~ 5. 6.</li><li>구민의 날 기념식 2026. 10. 1. ~ 10. 2.</li>"
              f"<li>{NAME} 2026. 4. 4. ~ 4. 5.</li></ul>")
    assert blocker(decide("https://www.ddm.go.kr/tour/contents.do?key=1400", NAME, pg)) == ("unverifiable", "LIST_PAGE")


def test_s4_list_page_where_neighbours_are_spelled_with_different_lead_words():
    """agy 3 — 같은 이웃 축제가 앞 낱말만 달리 두 번 나오면 한 이름으로 센다."""
    sib = "".join(f"<p>{x}</p><p>5월 전통 {x}</p>" for x in ("청룡문화제", "한방문화축제", "배봉산 해맞이 축제"))
    pg = page(f"{NAME} - 문화관광", "문화", f"<h2>{NAME}</h2><p>{NAME}</p><p>기간 : 2026. 4. 4. ~ 4. 5.</p>{sib}")
    assert blocker(decide("https://www.ddm.go.kr/tour/contents.do?key=1400", NAME, pg)) == ("unverifiable", "LIST_PAGE")


def test_generic_filter_does_not_drop_the_name_without_its_trailing_parenthesis():
    """보통 낱말 걸러내기는 연도 · 회차를 뗀 이름에만 선다 — 끝의 괄호를 뗀 이름(「우래옥 (본점)」 → 「우래옥」)은 예전대로 견준다."""
    assert _name_in("우래옥 (본점)", "우래옥 안내") and _name_whole_in("봄꽃축제 (동대문구)", "봄꽃축제 안내")
    v = decide("https://www.wooraeok.co.kr/", "우래옥 (본점)", page("우래옥", "우래옥", "x"), official_domain="wooraeok.co.kr")
    assert (v.outcome, v.match_rule) == ("accepted", "exact")


def test_s4_intro_page_with_one_range_and_side_dates_is_not_a_list():
    """목록 판정의 날짜 세기 — 한 줄의 범위(「2026. 4. 4. ~ 2026. 4. 5.」)는 하루로 세고, 앞 칸이 「접수 · 수정」 인 날짜는 세지 않는다."""
    extra = ("<p>사전행사 2026. 4. 1.</p><table><tr><th>참가 접수</th><td>2026. 3. 2. ~ 2026. 3. 20.</td></tr>"
             "<tr><th>최종 수정</th><td>2026. 11. 3.</td></tr></table>")
    v = decide(DDM, NAME, intro(date="<p>기간 : 2026. 4. 4. ~ 2026. 4. 5.</p>", extra=extra))
    assert (v.outcome, v.match_rule) == ("accepted", "S4")
