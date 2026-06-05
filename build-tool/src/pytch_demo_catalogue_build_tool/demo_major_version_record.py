from dataclasses import dataclass
from pathlib import Path

import pygit2

from .demo_locale_context import MultiLocaleDemo


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
