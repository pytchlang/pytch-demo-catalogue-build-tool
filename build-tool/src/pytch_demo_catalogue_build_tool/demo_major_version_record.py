from dataclasses import dataclass, asdict
from collections import defaultdict
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
    entry_is_symlink,
    tree_entry_within_commit,
    file_within_commit,
    json_within_commit,
    text_within_commit,
    maybe_tree_entry_within_commit,
)


@dataclass
class DemoMajorVersionRecord(MultiLocaleDemo):
    _repo: pygit2.Repository
    uuid: str
    latest_uuid: Optional[str]
    _defining_commit_id: str
    _demo_root_path: Path
    _effective_mtime_commit_id: str
    _present_at_head: bool

    @property
    def status_str(self) -> str:
        if self.present_at_head:
            return "live"
        if self.latest_uuid is None:
            return "gone"
        return f"superseded by {self.latest_uuid}"

    def pprint_str(self) -> str:
        return (
            f"{self.uuid} {self._demo_root_path} ({self.status_str})"
        )

    def within_group_sort_key(self) -> str:
        # Make the latest version sort after any earlier version.
        if self._present_at_head:
            return "1"
        else:
            return f"0-{self.uuid}"

    @staticmethod
    def grouped_by_latest(
            records: list["DemoMajorVersionRecord"]
    ) -> list["DemoMajorVersionRecord"]:
        records_by_latest: defaultdict[
            Optional[str], list["DemoMajorVersionRecord"]
        ] = defaultdict(list)
        for record in records:
            records_by_latest[record.latest_uuid].append(record)

        for uuid_records in records_by_latest.values():
            uuid_records.sort(
                key=DemoMajorVersionRecord.within_group_sort_key
            )

        # When sorting, map "None" as a latest-uuid (i.e., deleted
        # demos) to appear first.
        latest_with_records = sorted(
            records_by_latest.items(),
            key=lambda kv: kv[0] or ""
        )

        grouped_records: list["DemoMajorVersionRecord"] = []
        for _latest_uuid, group in latest_with_records:
            grouped_records.extend(group)

        start_uuids = set(r.uuid for r in records)
        final_uuids = set(r.uuid for r in grouped_records)
        assert start_uuids == final_uuids

        return grouped_records

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

    def _commit(self, sha1: str) -> pygit2.Commit:
        repo_obj = self.repo[sha1]
        if not isinstance(repo_obj, pygit2.Commit):
            raise RuntimeError(f"id {sha1} did not give Commit")
        return repo_obj

    @property
    def commit(self) -> pygit2.Commit:
        return self._commit(self.defining_commit_id)

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

    @property
    def effective_mtime_str(self) -> str:
        mtime_commit = self._commit(self._effective_mtime_commit_id)
        mtime = time.gmtime(mtime_commit.author.time)
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", mtime)

    def catalogue_entry(self, locale_code: str) -> CatalogueEntry:
        ctx = LocaleContext(self, locale_code)

        ZipfileMetadataKeys = constants.PytchZipfileMetadataKeys
        display_name = ctx.project_metadata[ZipfileMetadataKeys.Project_Name]

        last_updated = self.effective_mtime_str

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

    def _copy_tree_entries(
        self, tree: pygit2.Tree, repo_path: Path, dist_path: Path
    ) -> None:
        dist_path.mkdir(parents=True, exist_ok=True)
        for entry in tree:
            name = name_of_tree_entry(entry)
            entry_dist_path = dist_path / name
            match entry.type_str:
                case "tree":
                    entry_tree: pygit2.Tree = entry  # type: ignore
                    self._copy_tree_entries(
                        entry_tree, repo_path / name, entry_dist_path
                    )
                case "blob" if entry_is_symlink(entry):
                    # The dist is served as plain files, so a symlink in
                    # the repo (used to share, say, one image between
                    # locales) is written out as a copy of its target.
                    self.copy_file(repo_path / name, entry_dist_path)
                case "blob":
                    entry_blob: pygit2.Blob = entry  # type: ignore
                    entry_dist_path.write_bytes(entry_blob.data)
                case entry_type:
                    raise RuntimeError(
                        f'expecting "tree" or "blob" for "{name}"'
                        f' in "{repo_path}" within tree of'
                        f" commit {self.defining_commit_id}"
                        f' but found "{entry_type}"'
                    )

    def copy_tree(self, repo_path: Path, dist_path: Path) -> None:
        """Recursively copy a directory out of this demo's defining commit.

        Everything under the tree `repo_path` within the tree of the
        defining commit is written to the directory `dist_path`, which
        is created (along with any missing parents) if it does not
        already exist.  A symlink is copied as its target's contents,
        rather than as the link itself.
        """
        tree: pygit2.Tree = tree_entry_within_commit(  # type: ignore
            self.repo, self.defining_commit_id, repo_path, "tree"
        )
        self._copy_tree_entries(tree, repo_path, dist_path)

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
                assets/  [if present, and everything under it]
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

        assets_path = ctx.repo_content_assets_tree_path
        assets_tree = maybe_tree_entry_within_commit(
            self.repo, self.defining_commit_id, assets_path
        )
        if assets_tree is not None and assets_tree.type_str == "tree":
            dest_path = dist_locale_root_dir / LocaleContent.ContentAssets_Dir
            self.copy_tree(assets_path, dest_path)

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
