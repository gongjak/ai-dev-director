"""포크 5차 리뷰(codex) 지적의 재현 시험 — 참조 폴더(3).

수집 단계 쪽(1 · 2)은 tests/test_collector_review5.py 에 있다.
"""
import pytest

from core.builder_context import _secret_content, referenced_documents
from schemas.order import OrderSchema

MARK = "REDACTED_TEST_MARKER"


# ------------------------------------------------------------------ 3. 문서(.md)의 따옴표 없는 `키: 값`
@pytest.mark.parametrize("suffix", [".md", ".txt", ".yaml", ".json", ".toml", ".rst", ""])
@pytest.mark.parametrize("line", [f"production_password: {MARK}", f"API_KEY={MARK}", f"- db_token : {MARK}",
                                  f"refresh_token: {MARK}"])
def test_3_unquoted_secret_assignment_is_caught_in_every_non_code_file(suffix, line):
    assert _secret_content(suffix, f"# 배포 메모\n\n{line}\n")


@pytest.mark.parametrize("suffix", [".py", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"])
def test_3_code_keeps_the_quoted_rule(suffix):
    # 코드는 그대로다 — 매개변수 주석 · 함수 호출 대입은 비밀이 아니고, 따옴표 대입은 비밀이다
    assert not _secret_content(suffix, "def login(password: str, token=None):\n    token = next_token()\n")
    assert _secret_content(suffix, f"PASSWORD = '{MARK}'\n")


def test_3_prose_that_only_mentions_the_word_still_passes():
    assert not _secret_content(".md", "비밀번호는 환경변수로 넘긴다. password 를 코드에 적지 않는다.\n")


def test_3_snapshot_lists_the_markdown_file_by_name_only(tmp_path):
    hub = tmp_path / "roots" / "hub"
    hub.mkdir(parents=True)
    (hub / "DEPLOY.md").write_text(f"# 배포\n\nproduction_password: {MARK}\n", encoding="utf-8")
    (hub / "README.md").write_text("password 를 코드에 적지 않는다.\n", encoding="utf-8")
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), [tmp_path / "roots"])
    assert MARK not in "".join(docs.values())
    assert [k for k in docs if k.endswith(".md")] == [str(hub / "README.md")]
    assert "DEPLOY.md" in docs[str(hub) + "/"]                          # 이름은 안내문에 남는다
