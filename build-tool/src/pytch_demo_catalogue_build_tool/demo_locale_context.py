from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pygit2

from . import constants
from .repo_files import thumbnail_with_extension, maybe_thumbnail_with_extension


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

    @property
    def repo_locale_root(self) -> Path:
        return (
            self.demo.demo_root_path
            / constants.DemoRepoPaths.Locales_Dir
            / self.locale_code
        )

    @property
    def repo_project_path(self) -> Path:
        return self.repo_locale_root / constants.DemoRepoPaths.LocaleContent.Project_Dir

    @property
    def repo_project_metadata_path(self) -> Path:
        return (
            self.repo_project_path / constants.PytchZipfilePaths.Project_Metadata_File
        )

    @property
    def repo_content_dir_path(self) -> Path:
        return self.repo_locale_root / constants.DemoRepoPaths.LocaleContent.Content_Dir

    @property
    def repo_metadata_path(self) -> Path:
        return (
            self.repo_locale_root / constants.DemoRepoPaths.LocaleContent.Metadata_File
        )

    @property
    def repo_description_path(self) -> Path:
        return (
            self.repo_content_dir_path
            / constants.DemoRepoPaths.LocaleContent.Description_File
        )

    @property
    def repo_summary_path(self) -> Path:
        return (
            self.repo_content_dir_path
            / constants.DemoRepoPaths.LocaleContent.Summary_File
        )

    @property
    def thumb_image_path(self) -> Path:
        return thumbnail_with_extension(
            self.demo.repo,
            self.demo.defining_commit_id,
            self.repo_content_dir_path,
            "image",
            constants.image_extensions,
        )

    @property
    def maybe_thumb_video_path(self) -> Optional[Path]:
        return maybe_thumbnail_with_extension(
            self.demo.repo,
            self.demo.defining_commit_id,
            self.repo_content_dir_path,
            "video",
            constants.video_extensions,
        )

    @property
    def dist_rel_path(self) -> Path:
        return Path(self.locale_code)

    @property
    def dist_rel_zipfile_path(self) -> Path:
        return self.dist_rel_path / constants.DistPaths.LocaleContent.Zipfile_File

    @property
    def repo_project_relative_paths(self) -> list[Path]:
        assets_metadata_file = (
            self.repo_project_path / constants.PytchZipfilePaths.Assets_Metadata_File
        )
        asset_records = self.json_list(assets_metadata_file)
        asset_files = [
            constants.PytchZipfilePaths.Asset_Files_Dir
            / record[constants.PytchZipfileAssetMetadataKeys.Filename]
            for record in asset_records
        ]
        return constants.PytchZipfilePaths.Fixed_Files + asset_files
