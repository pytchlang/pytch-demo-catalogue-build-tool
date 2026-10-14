"""Tests for ``remove_demo_locale``, which removes a locale of a demo
and gives the demo a new uuid (i.e., a new major version)."""

from pathlib import Path

import pygit2
import pytest

from pytch_demo_catalogue_build_tool import copy_demo_locale
from pytch_demo_catalogue_build_tool.remove_demo_locale import main
from pytch_demo_catalogue_build_tool.repo_builder import (
    DEFAULT_HISTORY,
    FileState,
    _resolve_demo_spec,
    load_history,
)
from pytch_demo_catalogue_build_tool.version_data import Extractor

OLD_UUID = "f0000000-0000-4000-8000-000000000001"

SIG = pygit2.Signature("Test Author", "test@example.com", 1_700_000_000, 0)


def _make_demo(tmp_path: Path, locales: list[str]) -> Path:
    """A complete demo in `locales`, committed to and checked out of a
    fresh git repo."""
    op = {"demo": "x", "uuid": OLD_UUID, "programKind": "flat", "locales": locales}
    spec = _resolve_demo_spec(op, load_history(DEFAULT_HISTORY), "message")
    state = FileState()
    state.put_demo("demos/demo", spec)

    repo_path = tmp_path / "repo"
    repo = pygit2.init_repository(str(repo_path))
    repo.create_commit("HEAD", SIG, SIG, "add demo", state.write_tree(repo), [])
    repo.checkout_head(strategy=pygit2.enums.CheckoutStrategy.FORCE)
    return repo_path / "demos" / "demo"


def _commit(repo: pygit2.Repository, message: str) -> str:
    repo.index.add_all()
    repo.index.write()
    tree_id = repo.index.write_tree()
    parents = [repo.head.target]
    return str(repo.create_commit("HEAD", SIG, SIG, message, tree_id, parents))


def _uuid(demo_dir: Path) -> str:
    return (demo_dir / "pytch-demo-uuid.txt").read_text().strip()


def test_locale_removed_and_uuid_replaced(tmp_path: Path) -> None:
    demo_dir = _make_demo(tmp_path, ["en", "ga"])
    main(demo_dir, "ga")

    assert not (demo_dir / "by-locale" / "ga").exists()
    assert (demo_dir / "by-locale" / "en").is_dir()
    new_uuid = _uuid(demo_dir)
    assert new_uuid not in ("", OLD_UUID)


def test_result_passes_locale_check(tmp_path: Path) -> None:
    """Once committed, the old uuid keeps the removed locale."""
    demo_dir = _make_demo(tmp_path, ["en", "ga"])
    repo = pygit2.Repository(str(demo_dir.parent.parent))
    with_ga_commit = str(repo.head.target)

    main(demo_dir, "ga")
    _commit(repo, "without ga")

    extractor = Extractor(repo)
    new_uuid = _uuid(demo_dir)
    assert extractor.all_uuids == {OLD_UUID, new_uuid}
    assert extractor.chain_heads[OLD_UUID] == new_uuid
    assert extractor.defining_commits[OLD_UUID].sha1 == with_ga_commit


def test_removing_link_target_raises(tmp_path: Path) -> None:
    demo_dir = _make_demo(tmp_path, ["en"])
    copy_demo_locale.main(demo_dir, "en", "de")
    with pytest.raises(RuntimeError, match="symlinks point into"):
        main(demo_dir, "en")
    assert (demo_dir / "by-locale" / "en").is_dir()
    assert _uuid(demo_dir) == OLD_UUID


def test_only_locale_raises(tmp_path: Path) -> None:
    demo_dir = _make_demo(tmp_path, ["en"])
    with pytest.raises(RuntimeError, match="only locale"):
        main(demo_dir, "en")


def test_missing_locale_raises(tmp_path: Path) -> None:
    demo_dir = _make_demo(tmp_path, ["en", "ga"])
    with pytest.raises(RuntimeError, match="not a directory"):
        main(demo_dir, "de")


def test_non_demo_raises(tmp_path: Path) -> None:
    demo_dir = _make_demo(tmp_path, ["en", "ga"])
    (demo_dir / "pytch-demo-uuid.txt").unlink()
    with pytest.raises(RuntimeError, match="is not a demo"):
        main(demo_dir, "ga")
