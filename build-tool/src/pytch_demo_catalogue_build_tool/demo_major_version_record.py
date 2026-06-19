from dataclasses import dataclass, asdict
import json
from pathlib import Path
import time
from typing import Any, Optional
import zipfile

import pygit2

from .demo_catalogue_entry import CatalogueEntry, IndexRecord
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
    latest_uuid: Optional[str]
    _defining_commit_id: str
    _demo_root_path: Path
    _present_at_head: bool

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
    def present_at_head(self) -> bool:
        """True iff this demo-major-version exists in HEAD's tree.

        This is the test for whether the demo is currently
        discoverable: deleted demos survive in the history (and so
        still have their explanatory content written out for extant
        linked projects) but do not appear in the index, and do not
        have a fresh project zip or thumbnails written.
        """
        return self._present_at_head

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

    def index_contributions(self) -> list[IndexRecord]:
        return (
            []
            if not self.present_at_head
            else [
                (locale_code, self.catalogue_entry(locale_code))
                for locale_code in self.locale_codes()
            ]
        )

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

    def catalogue_entry(self, locale_code: str) -> CatalogueEntry:
        ctx = LocaleContext(self, locale_code)

        ZipfileMetadataKeys = constants.PytchZipfileMetadataKeys
        display_name = ctx.project_metadata[ZipfileMetadataKeys.Project_Name]

        modify_time = self.commit.author.time
        last_updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(modify_time))

        summary_markdown = self.text_within_commit(ctx.repo_summary_path)

        global_metadata = self.global_metadata
        author_name = global_metadata[constants.DemoMetadataKeys.Author_Name]
        demo_kind = global_metadata[constants.DemoMetadataKeys.Demo_Kind]

        recommended = ctx.metadata[constants.DemoMetadataKeys.Is_Recommended]

        program_kind = self.common_program_kind()

        thumbnail_image_extension = ctx.thumb_image_path.suffix
        m_thumb_vid_path = ctx.maybe_thumb_video_path
        thumbnail_video_extension = m_thumb_vid_path and m_thumb_vid_path.suffix

        return CatalogueEntry(
            self.uuid,
            display_name,
            author_name,
            program_kind,
            demo_kind,
            summary_markdown,
            last_updated,
            recommended,
            thumbnail_image_extension,
            thumbnail_video_extension,
            self.latest_uuid,
        )

    def copy_file(self, repo_path: Path, dist_path: Path) -> None:
        data = self.file_within_commit(repo_path)
        dist_path.write_bytes(data)

    def write_locale_dist_files(self, dist_demo_root: Path, locale_code: str) -> None:
        """
        Write files within the directory

        * `${DIST_ROOT}/${DEMO_UUID}/${locale_code}`

        where we are given

        * `dist_demo_root = ${DIST_ROOT}/${DEMO_UUID}`

        Relative to `${dist_demo_root}/${locale_code}`, we write:

        ```
            metadata.json
            project.zip (**)
            content/
                description.md
                thumbnail.jpg (**)
                thumbnail.mp4 (**)
        ```

        where the files marked (**) are only included if `self` is the
        "current major version" of a particular demo.

        This is fiddly but not complicated.
        """
        ctx = LocaleContext(self, locale_code)
        LocaleContent = constants.DistPaths.LocaleContent

        # Create directory structure within dist dir.
        dist_locale_root_dir = dist_demo_root / locale_code
        dist_content_dir = dist_locale_root_dir / LocaleContent.Content_Dir
        dist_content_dir.mkdir(parents=True, exist_ok=True)

        # Compute and write locale-specific metadata file.
        dist_metadata_path = dist_locale_root_dir / LocaleContent.Metadata_File
        catalogue_entry = self.catalogue_entry(locale_code)
        catalogue_entry_json = json.dumps(asdict(catalogue_entry), indent=2)
        dist_metadata_path.write_text(catalogue_entry_json)

        # Copy "description" and "summary" markdown files.
        description_path = dist_content_dir / LocaleContent.Description_File
        description_path.write_bytes(ctx.repo_description_data)

        # TODO: Assets used in "description" markdown.

        if self.present_at_head:
            # Also need thumbnails and project zipfile.

            # Thumbnail image.
            thumb_img_basename = catalogue_entry.thumb_image_basename
            thumb_img_repo_path = ctx.repo_content_dir_path / thumb_img_basename
            thumb_img_dist_path = dist_content_dir / thumb_img_basename
            self.copy_file(thumb_img_repo_path, thumb_img_dist_path)

            # Thumbnail video, if there is one.
            thumb_vid_basename = catalogue_entry.maybe_thumb_video_basename
            if thumb_vid_basename is not None:
                thumb_vid_repo_path = ctx.repo_content_dir_path / thumb_vid_basename
                thumb_vid_dist_path = dist_content_dir / thumb_vid_basename
                self.copy_file(thumb_vid_repo_path, thumb_vid_dist_path)

            # Project zipfile.
            dist_zipfile_path = dist_demo_root / ctx.dist_rel_zipfile_path
            with dist_zipfile_path.open("wb") as f_zip:
                with zipfile.ZipFile(f_zip, "w") as zip:
                    for rel_path in ctx.repo_project_relative_paths:
                        repo_path = ctx.repo_project_path / rel_path
                        repo_data = self.file_within_commit(repo_path)
                        zip.writestr(str(rel_path), repo_data)

    def write_dist_files(self, dist_root: Path) -> None:
        dist_dir = dist_root / self.uuid
        dist_dir.mkdir(parents=True, exist_ok=True)
        for locale_code in self.locale_codes():
            self.write_locale_dist_files(dist_dir, locale_code)
