from pathlib import Path

import pygit2


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
