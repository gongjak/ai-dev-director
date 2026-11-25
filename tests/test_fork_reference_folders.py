"""포크: ORDER 가 가리키는 참조 경로 — `~` 로 쓴 문서 경로와 reference_roots 아래 폴더 스냅샷."""
import os
from pathlib import Path

import pytest

from core import builder_context
from core.builder_context import referenced_documents
from schemas.order import OrderSchema

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX 경로 표기")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME 을 임시 폴더로 돌린다 — `~` 가 실제 홈을 읽지 않게."""
    h = tmp_path / "home"
    (h / "Developer" / "oss" / "map-hub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def _tree(folder: Path):
    """폴더 안 모든 항목의 (상대경로, 크기, mtime) — 읽기 전용임을 재는 데 쓴다."""
    return sorted((str(p.relative_to(folder)), p.lstat().st_size, p.lstat().st_mtime_ns) for p in folder.rglob("*"))


# ---------------------------------------------------------------- 1. `~` 경로
@posix_only
def test_tilde_document_path_in_text_is_expanded_and_read(home):
    doc = home / "Developer" / "oss" / "map-hub" / "README.md"
    doc.write_text("MAP HUB", encoding="utf-8")
    order = OrderSchema(goal="~/Developer/oss/map-hub/README.md 를 읽고 요약한다")
    assert referenced_documents(order, [home / "Developer" / "oss"]) == {str(doc): "MAP HUB"}


@posix_only
def test_tilde_reference_path_is_expanded(home):
    doc = home / "Developer" / "oss" / "map-hub" / "README.md"
    doc.write_text("MAP HUB", encoding="utf-8")
    order = OrderSchema(goal="g", reference_paths=["~/Developer/oss/map-hub/README.md"])
    assert referenced_documents(order, [home / "Developer" / "oss"]) == {str(doc): "MAP HUB"}


@posix_only
def test_tilde_document_outside_reference_roots_is_refused(home):
    (home / "secret.md").write_text("PRIVATE", encoding="utf-8")
    with pytest.raises(ValueError, match="reference_roots"):
        referenced_documents(OrderSchema(goal="~/secret.md 를 읽는다"), [home / "Developer" / "oss"])


@posix_only
def test_tilde_inside_a_word_or_other_user_is_not_a_path(home):
    (home / "notes.md").write_text("N", encoding="utf-8")
    order = OrderSchema(goal="g", context="크기는 a~/notes.md 쯤이고 ~root/notes.md 는 남의 홈이다. 상대경로 docs/~/x.md 도 아니다.")
    assert referenced_documents(order, [home]) == {}


def test_autopilot_keeps_tilde_reference_paths(home):
    from core.autopilot import sanitize_order
    doc = home / "Developer" / "oss" / "map-hub" / "README.md"
    doc.write_text("MAP HUB", encoding="utf-8")
    order, _ = sanitize_order(OrderSchema(goal="g", allowed_paths=["src/**"],
                                           reference_paths=["~/Developer/oss/map-hub/README.md"]), str(home))
    assert order.reference_paths == [str(doc)]


# ---------------------------------------------------------------- 2. 폴더 스냅샷
def _hub(home):
    hub = home / "Developer" / "oss" / "map-hub"
    (hub / "src").mkdir()
    (hub / "README.md").write_text("MAP HUB", encoding="utf-8")
    (hub / "src" / "main.py").write_text("print('hub')\n", encoding="utf-8")
    (hub / "src" / "app.cjs").write_text("module.exports = 1\n", encoding="utf-8")
    (hub / "package.json").write_text("{}", encoding="utf-8")
    # 넘기면 안 되는 것들
    (hub / ".env").write_text("API_KEY=x", encoding="utf-8")
    (hub / ".env.local").write_text("API_KEY=y", encoding="utf-8")
    (hub / "server.pem").write_text("-----BEGIN", encoding="utf-8")
    (hub / "api_key.txt").write_text("k", encoding="utf-8")
    (hub / "credentials.json").write_text("{}", encoding="utf-8")
    (hub / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    (hub / "blob.txt").write_bytes(b"\xff\xfe\x00binary")
    for skipped in (".git", "node_modules", ".venv"):
        (hub / skipped).mkdir()
        (hub / skipped / "inner.md").write_text("SKIP", encoding="utf-8")
    return hub


def test_folder_in_reference_paths_gives_bounded_text_snapshot_and_never_writes(home):
    hub = _hub(home)
    before = _tree(hub)
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), [home / "Developer" / "oss"])
    files = {k: v for k, v in docs.items() if not k.endswith("/")}
    assert files == {str(hub / "README.md"): "MAP HUB", str(hub / "package.json"): "{}",
                     str(hub / "src" / "app.cjs"): "module.exports = 1\n", str(hub / "src" / "main.py"): "print('hub')\n"}
    manifest = docs[str(hub) + "/"]
    assert "읽기 전용" in manifest and "4" in manifest
    assert _tree(hub) == before                      # 참조 폴더에는 아무것도 쓰지 않는다


@posix_only
def test_folder_named_in_text_with_tilde_is_snapshotted(home):
    hub = _hub(home)
    order = OrderSchema(goal="~/Developer/oss/map-hub 폴더의 구조를 참고해 같은 방식으로 만든다.")
    docs = referenced_documents(order, [home / "Developer" / "oss"])
    assert docs[str(hub / "src" / "main.py")] == "print('hub')\n"


@posix_only
def test_folder_outside_reference_roots_fails_closed(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("A", encoding="utf-8")
    roots = [home / "Developer" / "oss"]
    with pytest.raises(ValueError, match="reference_roots"):
        referenced_documents(OrderSchema(goal="g", reference_paths=[str(outside)]), roots)
    # 4차 리뷰 A — goal 에 이름만 나온 루트 밖 폴더는 실행을 죽이지 않고 건너뛴다(내용은 나가지 않는다)
    assert referenced_documents(OrderSchema(goal=f"{outside} 폴더를 참고한다"), roots) == {}


@posix_only
def test_symlinked_folder_and_symlinks_inside_are_not_followed(home, tmp_path):
    hub = _hub(home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("LEAK", encoding="utf-8")
    (hub / "linkdir").symlink_to(outside, target_is_directory=True)
    (hub / "link.md").symlink_to(outside / "leak.md")
    roots = [home / "Developer" / "oss"]
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), roots)
    assert "LEAK" not in "".join(docs.values())
    alias = home / "Developer" / "oss" / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
        referenced_documents(OrderSchema(goal="g", reference_paths=[str(alias)]), roots)


def test_folder_snapshot_is_capped_by_file_count_and_bytes(home, monkeypatch):
    hub = home / "Developer" / "oss" / "map-hub"
    for i in range(12):
        (hub / f"f{i:02d}.md").write_text("x" * 1000, encoding="utf-8")
    (hub / "huge.md").write_text("y" * 5000, encoding="utf-8")
    roots = [home / "Developer" / "oss"]
    monkeypatch.setattr(builder_context, "FOLDER_MAX_FILES", 5)
    monkeypatch.setattr(builder_context, "FOLDER_FILE_MAX_BYTES", 4000)
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), roots)
    files = [k for k in docs if not k.endswith("/")]
    assert [Path(k).name for k in files] == ["f00.md", "f01.md", "f02.md", "f03.md", "f04.md"]   # 정렬 순서로 자른다
    assert "f05.md" in docs[str(hub) + "/"]                                                    # 빠진 것은 이름만 알린다
    monkeypatch.setattr(builder_context, "FOLDER_MAX_FILES", 100)
    monkeypatch.setattr(builder_context, "FOLDER_MAX_CHARS", 3500)
    docs = referenced_documents(OrderSchema(goal="g", reference_paths=[str(hub)]), roots)
    files = [k for k in docs if not k.endswith("/")]
    assert len(files) == 3 and "huge.md" not in "".join(files)


@posix_only
def test_prose_paths_urls_and_missing_folders_are_not_folder_references(home):
    _hub(home)
    order = OrderSchema(goal="g", context=(
        "API 는 http://127.0.0.1:8080/status 와 /api/v1/없는경로 를 쓴다. 상대경로 docs/ 와 src/main 은 저장소 안이다. "
        "/없는/폴더/여기 는 없다."))
    assert referenced_documents(order, [home / "Developer" / "oss"]) == {}


@posix_only
def test_project_folder_itself_is_not_snapshotted_again(home):
    hub = _hub(home)
    order = OrderSchema(goal=f"프로젝트 {hub} 에서 README 를 고친다. 하위 {hub}/src 도 본다.")
    assert referenced_documents(order, [hub, home / "Developer" / "oss"], project_roots=[hub]) == {}


@posix_only
def test_text_folders_are_ignored_without_roots(home):
    hub = _hub(home)
    assert referenced_documents(OrderSchema(goal=f"{hub} 를 참고한다")) == {}
