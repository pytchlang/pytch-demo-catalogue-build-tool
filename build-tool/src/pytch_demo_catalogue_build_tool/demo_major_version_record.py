from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygit2

from . import constants
from .demo_locale_context import MultiLocaleDemo, LocaleContext
from .repo_files import (
    name_of_tree_entry,
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

    def locale_codes(self) -> list[str]:
        locales_path = str(self.demo_root_path / constants.DemoRepoPaths.Locales_Dir)
        locales_dir_obj = self.tree / locales_path
        if locales_dir_obj.type_str != "tree":
            raise RuntimeError(
                f'"{locales_path}" is not a tree within tree of'
                f" commit {self.defining_commit_id}"
            )

        locales_dir: pygit2.Tree = locales_dir_obj  # type: ignore
        locale_codes: list[str] = []
        for entry in locales_dir:
            name = name_of_tree_entry(entry)
            if entry.type_str != "tree":
                raise RuntimeError(
                    f'"{name}" is not a tree in {locales_path}'
                    f" within tree of commit {self.defining_commit_id}"
                )
            locale_codes.append(name)

        return locale_codes

    def locale_program_kind(self, locale_code: str) -> str:
        ctx = LocaleContext(self, locale_code)
        code_path = ctx.repo_project_path / constants.PytchZipfilePaths.Code_File
        code_obj = self.json_dict_within_commit(code_path)
        kind_obj = code_obj[constants.PytchZipfileCodeKeys.Program_Kind]
        if not isinstance(kind_obj, str):
            raise RuntimeError(
                f'program-kind in "{code_path}" within tree of'
                f" commit {self.defining_commit_id} is not a string"
            )
        return kind_obj

    def common_program_kind(self) -> str:
        program_kinds = set(map(self.locale_program_kind, self.locale_codes()))
        n_kinds = len(program_kinds)
        if n_kinds != 1:
            raise RuntimeError(
                f"found {n_kinds} distinct program-kind values"
                f' for "{self.demo_root_path}" within tree'
                f" of commit {self.defining_commit_id}"
            )
        return program_kinds.pop()

    @property
    def global_metadata(self) -> dict[str, Any]:
        path = self.demo_root_path / constants.DemoRepoPaths.Global_Metadata_File
        return self.json_dict_within_commit(path)
