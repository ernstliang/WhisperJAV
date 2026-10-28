"""Server-side directory browsing."""

import os
import sys

import pytest

from whisperjav.webserver import browse
from whisperjav.webserver.paths import normalize_path


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.mp4").write_bytes(b"x")
    (tmp_path / "a.mp4").write_bytes(b"x" * 10)
    (tmp_path / "b.srt").write_text("1\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("no", encoding="utf-8")
    (tmp_path / ".hidden.mp4").write_bytes(b"x")
    return tmp_path


def names(entries):
    return sorted(e["name"] for e in entries)


def test_empty_path_returns_the_root_view():
    data = browse.list_dir("")
    assert data["path"] is None
    assert data["is_root"] is True
    assert data["roots"], "at least one drive/root must be listed"
    if sys.platform == "win32":
        assert any(r["path"].endswith(":\\") for r in data["roots"])
    else:
        assert data["roots"][0]["path"] == "/"


def test_kind_media_filters_files(tree):
    data = browse.list_dir(str(tree), kind="media")
    assert names(data["files"]) == ["a.mp4"]
    assert names(data["dirs"]) == ["sub"]  # dirs are listed regardless of kind


def test_kind_srt_filters_files(tree):
    data = browse.list_dir(str(tree), kind="srt")
    assert names(data["files"]) == ["b.srt"]


def test_kind_all_lists_everything_visible(tree):
    data = browse.list_dir(str(tree), kind="all")
    assert names(data["files"]) == ["a.mp4", "b.srt", "c.txt"]


def test_hidden_files_excluded_by_default(tree):
    assert ".hidden.mp4" not in names(browse.list_dir(str(tree), kind="media")["files"])
    shown = browse.list_dir(str(tree), kind="media", show_hidden=True)["files"]
    assert ".hidden.mp4" in names(shown)


def test_file_metadata_present(tree):
    entry = browse.list_dir(str(tree), kind="media")["files"][0]
    assert entry["size"] == 10
    assert entry["mtime"] > 0
    assert entry["path"] == str(tree / "a.mp4")


def test_missing_path_is_a_client_error():
    with pytest.raises(browse.BrowseError) as exc:
        browse.list_dir(r"Q:\definitely\not\here" if sys.platform == "win32"
                        else "/definitely/not/here")
    assert exc.value.status == 400
    assert exc.value.code == "not_found"


def test_file_path_is_rejected_as_a_directory(tree):
    with pytest.raises(browse.BrowseError) as exc:
        browse.list_dir(str(tree / "a.mp4"))
    assert exc.value.code == "not_a_directory"


def test_parent_navigation(tree):
    data = browse.list_dir(str(tree / "sub"))
    assert data["parent"] == str(tree)


@pytest.mark.skipif(sys.platform != "win32", reason="drive-root semantics")
def test_parent_of_a_drive_root_is_the_drive_list():
    """
    Path("D:\\").parent == Path("D:\\"), so without the explicit mapping the
    Up button would loop forever at a drive root.
    """
    root = browse.list_roots()[0]["path"]
    data = browse.list_dir(root)
    assert data["parent"] == ""
    assert data["is_root"] is True


def test_dot_dot_normalizes_without_crashing(tree):
    data = browse.list_dir(str(tree / "sub" / ".." ))
    assert data["path"] == str(tree)


def test_expand_dir_files_non_recursive(tree):
    files = browse.expand_dir_files(str(tree), kind="media", recursive=False)
    assert names(files) == ["a.mp4"]


def test_expand_dir_files_recursive(tree):
    files = browse.expand_dir_files(str(tree), kind="media", recursive=True)
    assert names(files) == ["a.mp4", "deep.mp4"]


def test_expand_rejects_a_file(tree):
    with pytest.raises(browse.BrowseError):
        browse.expand_dir_files(str(tree / "a.mp4"))


def test_entries_are_sorted_dirs_and_files_separately(tree):
    (tree / "Zebra").mkdir()
    (tree / "alpha.mp4").write_bytes(b"x")
    data = browse.list_dir(str(tree), kind="media")
    assert [d["name"] for d in data["dirs"]] == ["sub", "Zebra"]
    assert [f["name"] for f in data["files"]] == ["a.mp4", "alpha.mp4"]


def test_normalize_path_is_absolute_and_normalized(tree):
    p = normalize_path(str(tree / "sub" / ".." / "a.mp4"))
    assert p == tree / "a.mp4"
    assert os.path.isabs(str(p))
