"""Tests for ``copy_demo_locale``, which creates a new locale of a demo
as symlinks into an existing locale."""

import os
from pathlib import Path

import pygit2
import pytest

from pytch_demo_catalogue_build_tool.copy_demo_locale import main
from pytch_demo_catalogue_build_tool.repo_files import file_within_commit

# Contents of the source locale, keyed by path within "by-locale/en".
# A value is a regular file's contents, or a ("link", target) pair for
# a symlink.
SRC_LOCALE_SPEC: dict[str, bytes | tuple[str, str]] = {
    "metadata.json": b'{"recommended": true}\n',
    "content/description.md": b"# Description\n",
    "content/summary.md": b"Summary\n",
    "content/thumbnail.png": b"PNG",
    "content/assets/diagram.png": ("link", "../../../../shared/diagram.png"),
    "project/meta.json": b'{"name": "Demo"}\n',
    "project/code/code.json": b'{"kind": "flat"}\n',
}


@pytest.fixture
def demo_dir(tmp_path: Path) -> Path:
    demo_dir = tmp_path / "demos" / "demo"
    (demo_dir / "shared").mkdir(parents=True)
    (demo_dir / "shared" / "diagram.png").write_bytes(b"DIAGRAM")
    (demo_dir / "pytch-demo-uuid.txt").write_text("some-uuid\n")

    src_root = demo_dir / "by-locale" / "en"
    for rel_path, value in SRC_LOCALE_SPEC.items():
        path = src_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, tuple):
            path.symlink_to(value[1])
        else:
            path.write_bytes(value)

    return demo_dir


def test_files_become_relative_symlinks(demo_dir: Path) -> None:
    main(demo_dir, "en", "ga")

    ga_root = demo_dir / "by-locale" / "ga"
    for rel_path in SRC_LOCALE_SPEC:
        ga_path = ga_root / rel_path
        assert ga_path.is_symlink()
        assert not ga_path.parent.is_symlink()
        n_up = len(Path(rel_path).parts)
        exp_text = "/".join([".."] * n_up + ["en", rel_path])
        assert os.readlink(ga_path) == exp_text


def test_symlinks_resolve_to_source_content(demo_dir: Path) -> None:
    main(demo_dir, "en", "ga")

    en_root = demo_dir / "by-locale" / "en"
    ga_root = demo_dir / "by-locale" / "ga"
    for rel_path in SRC_LOCALE_SPEC:
        assert (ga_root / rel_path).read_bytes() == (en_root / rel_path).read_bytes()


def test_build_tool_follows_created_symlinks(demo_dir: Path) -> None:
    """Once committed, the new locale's files read as the source's."""
    main(demo_dir, "en", "ga")

    repo_root = demo_dir.parent.parent
    repo = pygit2.init_repository(str(repo_root))
    repo.index.add_all()
    repo.index.write()
    sig = pygit2.Signature("Test Author", "test@example.com", 1_700_000_000, 0)
    tree_id = repo.index.write_tree()
    commit_id = str(repo.create_commit("HEAD", sig, sig, "demo", tree_id, []))

    for rel_path in SRC_LOCALE_SPEC:
        en_path = Path("demos/demo/by-locale/en") / rel_path
        ga_path = Path("demos/demo/by-locale/ga") / rel_path
        assert file_within_commit(repo, commit_id, ga_path) == file_within_commit(
            repo, commit_id, en_path
        )


def test_existing_destination_raises(demo_dir: Path) -> None:
    (demo_dir / "by-locale" / "ga").mkdir()
    with pytest.raises(RuntimeError, match="exists"):
        main(demo_dir, "en", "ga")


def test_missing_source_raises(demo_dir: Path) -> None:
    with pytest.raises(RuntimeError, match="not a directory"):
        main(demo_dir, "de", "ga")


def test_same_locale_raises(demo_dir: Path) -> None:
    with pytest.raises(RuntimeError, match="must differ"):
        main(demo_dir, "en", "en")


def test_non_demo_raises(demo_dir: Path) -> None:
    (demo_dir / "pytch-demo-uuid.txt").unlink()
    with pytest.raises(RuntimeError, match="is not a demo"):
        main(demo_dir, "en", "ga")
