from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygit2


class MultiLocaleDemo(ABC):
    @property
    @abstractmethod
    def repo(self) -> pygit2.Repository: ...

    @property
    @abstractmethod
    def defining_commit_id(self) -> str: ...

    @property
    @abstractmethod
    def demo_root_path(self) -> Path: ...

    @abstractmethod
    def json_dict_within_commit(self, path: Path) -> dict[str, Any]: ...

    @abstractmethod
    def json_list_within_commit(self, path: Path) -> list[Any]: ...

    @abstractmethod
    def file_within_commit(self, path: Path) -> bytes: ...


@dataclass
class LocaleContext:
    demo: MultiLocaleDemo
    locale_code: str
