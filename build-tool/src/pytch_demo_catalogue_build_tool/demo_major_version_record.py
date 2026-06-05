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
