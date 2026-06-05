import json
from pathlib import Path
from typing import Optional

import pygit2

from . import constants


def name_of_tree_entry(entry: pygit2.Object) -> str:
    if entry.name is None:
        raise RuntimeError(f"Object {entry.id} has no name")
    return entry.name


def tree_entry_within_commit(
    repo: pygit2.Repository, commit_id: str, path: Path, exp_type: str
) -> pygit2.Tree | pygit2.Blob:
    if (commit := repo.get(commit_id)) is None:
        raise KeyError(f"commit {commit_id} not found in repo")

    entry = commit.tree
    n_parts = len(path.parts)
    for idx, path_part in enumerate(path.parts):
        try:
            next_entry = entry / path_part  # type: ignore
        except KeyError:
            raise RuntimeError(
                f'"{path_part}" not found in "{"/".join(path.parts[:idx])}"'
                f" within tree of commit {commit_id}"
            )
        is_last = idx == n_parts - 1
        this_exp_type = exp_type if is_last else "tree"
        if next_entry.type_str != this_exp_type:  # type: ignore
            raise RuntimeError(
                f"expecting {this_exp_type} at posn {idx} in path"
                f' when processing "{path}" within tree of commit {commit_id}'
            )
        entry = next_entry  # type: ignore

    return entry  # type: ignore


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
