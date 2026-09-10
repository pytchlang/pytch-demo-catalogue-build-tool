"""Tests for symlink handling in ``repo_files``.

A demo shares content between its locales by symlinking it (see
``doc/README.md``), so looking a path up within a commit has to follow
those links.  The cases here are corner ones -- chains, cycles, links to
directories, links out of the repo -- so rather than the full
demo-history fixture they use a tiny hand-built repo whose tree is
declared inline.
"""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from pytch_demo_catalogue_build_tool.repo_files import (
    entry_is_symlink,
    file_within_commit,
    tree_entry_within_commit,
)

# The tree built for these tests: a real file, links to it by various
# routes, and the cases which cannot be followed.  A value is
# a regular file's contents, or a (mode, target) pair for a symlink.
LINK = pygit2.enums.FileMode.LINK

TREE_SPEC: dict[str, bytes | tuple] = {
    "shared/pic.png": b"PIC",
    "shared/caption.md": (LINK, "../top-caption.md"),
    "top-caption.md": b"CAPTION",
    "en/pic.png": (LINK, "../shared/pic.png"),
    "en/assets": (LINK, "../shared"),
    "en/indirect.png": (LINK, "pic.png"),
    "en/dangling.png": (LINK, "../shared/absent.png"),
    "en/absolute.png": (LINK, "/etc/passwd"),
    "en/escaping.png": (LINK, "../../outside.png"),
    "loop/a": (LINK, "b"),
    "loop/b": (LINK, "a"),
}


@pytest.fixture(scope="module")
def commit_id(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """A repo containing one commit whose tree realises ``TREE_SPEC``."""
    repo = pygit2.init_repository(str(tmp_path_factory.mktemp("links") / "repo"))

    # Group the spec by directory, then write each directory's tree
    # bottom-up; the spec is only ever two levels deep.
    dirs: dict[str, dict[str, bytes | tuple]] = {"": {}}
    for path, value in TREE_SPEC.items():
        dir_name, _, base = path.rpartition("/")
        dirs.setdefault(dir_name, {})[base] = value

    def write_tree(entries: dict[str, bytes | tuple]) -> pygit2.Oid:
        builder = repo.TreeBuilder()
        for name, value in sorted(entries.items()):
            if isinstance(value, tuple):
                _mode, target = value
                builder.insert(name, repo.create_blob(target.encode()), LINK)
            else:
                builder.insert(
                    name, repo.create_blob(value), pygit2.enums.FileMode.BLOB
                )
        return builder.write()

    root = repo.TreeBuilder()
    for dir_name, entries in sorted(dirs.items()):
        if dir_name == "":
            continue
        root.insert(dir_name, write_tree(entries), pygit2.enums.FileMode.TREE)
    for name, value in sorted(dirs[""].items()):
        assert not isinstance(value, tuple)
        root.insert(name, repo.create_blob(value), pygit2.enums.FileMode.BLOB)

    sig = pygit2.Signature("Test Author", "test@example.com", 1_700_000_000, 0)
    oid = repo.create_commit("refs/heads/main", sig, sig, "links", root.write(), [])
    return repo, str(oid)


def test_file_symlink_is_followed(commit_id) -> None:
    repo, cid = commit_id
    assert file_within_commit(repo, cid, Path("en/pic.png")) == b"PIC"


def test_symlink_chain_is_followed(commit_id) -> None:
    """A link to a link resolves all the way to the file."""
    repo, cid = commit_id
    assert file_within_commit(repo, cid, Path("en/indirect.png")) == b"PIC"


def test_target_is_relative_to_the_link_directory(commit_id) -> None:
    """A link's target is read relative to the directory holding the link,
    as on disk: "shared/caption.md" points at "../top-caption.md", which is
    the top-level file, not one within "shared/"."""
    repo, cid = commit_id
    assert file_within_commit(repo, cid, Path("shared/caption.md")) == b"CAPTION"


def test_entry_without_filemode_raises(commit_id) -> None:
    """Only a tree entry carries a filemode, so an object read straight from
    the object database cannot be classified.  Calling it a regular file
    would silently write a link's text out as though it were content, so
    the ambiguity is raised rather than guessed at."""
    repo, cid = commit_id
    tree_entry = repo.get(cid).tree / "en" / "pic.png"
    assert entry_is_symlink(tree_entry)

    odb_entry = repo[tree_entry.id]
    assert odb_entry.filemode is None
    with pytest.raises(RuntimeError, match="no filemode"):
        entry_is_symlink(odb_entry)


@pytest.mark.parametrize("path", ["en/assets", "en/assets/pic.png"])
def test_directory_symlink_is_not_followed(commit_id, path) -> None:
    """Only symlinks to files are followed, whether the link is named
    directly or used as a directory part-way along a path."""
    repo, cid = commit_id
    with pytest.raises(RuntimeError, match="symlink"):
        tree_entry_within_commit(repo, cid, Path(path), "blob")


@pytest.mark.parametrize(
    "path, expected_error",
    [
        ("en/dangling.png", "not found"),
        ("en/absolute.png", "outside the repo"),
        ("en/escaping.png", "above the repo root"),
        ("loop/a", "too many symlinks"),
    ],
)
def test_unfollowable_symlink_raises(commit_id, path, expected_error) -> None:
    """A link we cannot resolve to content within the repo is an error in
    the repo, and is reported rather than silently copied as its own text."""
    repo, cid = commit_id
    with pytest.raises(RuntimeError, match=expected_error):
        tree_entry_within_commit(repo, cid, Path(path), "blob")
