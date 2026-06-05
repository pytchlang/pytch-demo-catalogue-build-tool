from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygit2

from . import constants
from .demo_locale_context import MultiLocaleDemo
from .repo_files import (
    file_within_commit,
    json_within_commit,
    text_within_commit,
)


@dataclass
class DemoMajorVersionRecord(MultiLocaleDemo):
    _repo: pygit2.Repository
    uuid: str
    latest_uuid: str
    _defining_commit_id: str
    _demo_root_path: Path

    @property
    def repo(self) -> pygit2.Repository:
        return self._repo

    @property
    def defining_commit_id(self) -> str:
        return self._defining_commit_id

    @property
    def demo_root_path(self) -> Path:
        return self._demo_root_path

    @property
    def is_latest(self) -> bool:
        return self.uuid == self.latest_uuid

    @property
    def commit(self) -> pygit2.Commit:
        repo_obj = self.repo[self.defining_commit_id]
        if not isinstance(repo_obj, pygit2.Commit):
            raise RuntimeError(f"id {self.defining_commit_id} did not give Commit")
        return repo_obj

    @property
    def tree(self) -> pygit2.Tree:
        return self.commit.tree

    def file_within_commit(self, path: Path) -> bytes:
        return file_within_commit(self.repo, self.defining_commit_id, path)

    def _json_within_commit(self, path: Path) -> constants.JsonThing:
        return json_within_commit(self.repo, self.defining_commit_id, path)

    def json_dict_within_commit(self, path: Path) -> dict[str, Any]:
        thing = self._json_within_commit(path)
        if not isinstance(thing, dict):
            raise RuntimeError(
                f'JSON in "{path}" within tree of'
                f" commit {self.defining_commit_id} is not a JSON object"
            )
        return thing

    def json_list_within_commit(self, path: Path) -> list[Any]:
        thing = self._json_within_commit(path)
        if not isinstance(thing, list):
            raise RuntimeError(
                f'JSON in "{path}" within tree of'
                f" commit {self.defining_commit_id} is not a JSON array"
            )
        return thing

    def text_within_commit(self, path: Path) -> str:
        return text_within_commit(self.repo, self.defining_commit_id, path)
