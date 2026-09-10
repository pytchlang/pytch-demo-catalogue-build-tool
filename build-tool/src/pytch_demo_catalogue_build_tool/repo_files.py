import json
import posixpath
from pathlib import Path, PurePosixPath
from typing import Optional

import pygit2

from . import constants


def name_of_tree_entry(entry: pygit2.Object) -> str:
    if entry.name is None:
        raise RuntimeError(f"Object {entry.id} has no name")
    return entry.name


def entry_is_symlink(entry: pygit2.Object) -> bool:
    """True iff `entry` is a symlink rather than a regular file.

    Git stores a symlink as a blob whose contents are the target path,
    so only the filemode tells the two apart.  `entry` must therefore
    have come from a tree.  One fetched straight from the object
    database has no filemode, and so cannot be classified; an error is
    raised in that case.
    """
    if entry.filemode is None:
        raise RuntimeError(f"Object {entry.id} has no filemode")
    return entry.filemode == pygit2.enums.FileMode.LINK


# Bound on the number of symlinks one lookup may follow, so a cycle
# ("a" -> "b" -> "a") raises rather than looping forever.  The value is
# arbitrary but should be high enough.
_MAX_SYMLINK_HOPS = 20


def _symlink_target_path(link_path: Path, target: str) -> Path:
    """The repo-relative path which the symlink at `link_path` names.

    As on disk, `target` is relative to the directory holding the
    link.  Only symlinks pointing within the repo can be followed.  An
    absolute target, or a relative one climbing above the repo root,
    is an error.
    """
    if PurePosixPath(target).is_absolute():
        raise RuntimeError(
            f'symlink "{link_path}" points outside the repo, at "{target}"'
        )

    resolved = posixpath.normpath(
        posixpath.join(link_path.parent.as_posix(), target)
    )

    if resolved == ".." or resolved.startswith("../"):
        raise RuntimeError(
            f'symlink "{link_path}" points above the repo root, at "{target}"'
        )

    return Path(resolved)


def maybe_tree_entry_within_commit(
    repo: pygit2.Repository, commit_id: str, path: Path
) -> pygit2.Tree | pygit2.Blob | None:
    """Return entry at `path` from the tree of commit `commit_id`.

    Every component of `path` other than the last must name a tree.  If
    one does not exist, or is not a tree, RuntimeError is raised.  The
    entry named by the last component need not exist, in which case None
    is returned.
    """
    if (commit := repo.get(commit_id)) is None:
        raise KeyError(f"commit {commit_id} not found in repo")

    entry: pygit2.Tree | pygit2.Blob = commit.tree
    n_parts = len(path.parts)
    for idx, path_part in enumerate(path.parts):
        is_last = idx == n_parts - 1
        try:
            next_entry = entry / path_part  # type: ignore
        except KeyError:
            if is_last:
                return None
            raise RuntimeError(
                f'"{path_part}" not found in "{"/".join(path.parts[:idx])}"'
                f" within tree of commit {commit_id}"
            )
        entry_type: str = next_entry.type_str  # type: ignore
        if not is_last and entry_type != "tree":
            raise RuntimeError(
                f'expecting "tree" at posn {idx} in path'
                f' when processing "{path}" within tree of commit {commit_id}'
                f' but found "{entry_type}"'
            )
        entry = next_entry  # type: ignore

    return entry  # type: ignore


def tree_entry_within_commit(
    repo: pygit2.Repository, commit_id: str, path: Path, exp_type: str
) -> pygit2.Tree | pygit2.Blob:
    """Return entry at `path` from the tree of commit `commit_id`.

    The entry named by the last component of `path` must exist and be of
    type `exp_type`.  RuntimeError is raised if not.
    """
    entry = maybe_tree_entry_within_commit(repo, commit_id, path)

    if entry is None:
        raise RuntimeError(
            f'"{path.parts[-1]}" not found in "{"/".join(path.parts[:-1])}"'
            f" within tree of commit {commit_id}"
        )

    entry_type: str = entry.type_str
    if entry_type != exp_type:
        raise RuntimeError(
            f'expecting "{exp_type}" as last component of path'
            f' when processing "{path}" within tree of commit {commit_id}'
            f' but found "{entry_type}"'
        )

    return entry


def file_within_commit(repo: pygit2.Repository, commit_id: str, path: Path) -> bytes:
    blob: pygit2.Blob = tree_entry_within_commit(repo, commit_id, path, "blob")  # type: ignore
    return blob.data


def text_within_commit(repo: pygit2.Repository, commit_id: str, path: Path) -> str:
    data = file_within_commit(repo, commit_id, path)
    return data.decode("utf-8")


def json_within_commit(
    repo: pygit2.Repository, commit_id: str, path: Path
) -> constants.JsonThing:
    json_data = file_within_commit(repo, commit_id, path)
    return json.loads(json_data)


def _thumbnail_with_extension(
    repo: pygit2.Repository,
    commit_id: str,
    dir: Path,
    kind_label: str,
    required: bool,
    extensions: list[str],
) -> Optional[Path]:
    tree_obj: pygit2.Tree = tree_entry_within_commit(repo, commit_id, dir, "tree")  # type: ignore

    found_path = None
    for candidate in tree_obj:
        candidate_path = Path(name_of_tree_entry(candidate))
        if (
            candidate_path.stem == constants.DemoRepoPaths.LocaleContent.Thumbnail_Stem
            and candidate_path.suffix in extensions
        ):
            if found_path is not None:
                raise RuntimeError(
                    f'found multiple {kind_label} thumbnails in "{dir}"'
                    f" within tree of {commit_id}"
                )
            found_path = candidate_path

    if found_path is None and required:
        raise RuntimeError(
            f'found no {kind_label} thumbnails in "{dir}"'
            f" within tree of {commit_id}"
        )

    return found_path


def thumbnail_with_extension(
    repo: pygit2.Repository,
    commit_id: str,
    dir: Path,
    kind_label: str,
    extensions: list[str],
) -> Path:
    thumbnail_path = _thumbnail_with_extension(
        repo, commit_id, dir, kind_label, True, extensions
    )
    return thumbnail_path  # type: ignore


def maybe_thumbnail_with_extension(
    repo: pygit2.Repository,
    commit_id: str,
    dir: Path,
    kind_label: str,
    extensions: list[str],
) -> Optional[Path]:
    return _thumbnail_with_extension(
        repo, commit_id, dir, kind_label, False, extensions
    )
