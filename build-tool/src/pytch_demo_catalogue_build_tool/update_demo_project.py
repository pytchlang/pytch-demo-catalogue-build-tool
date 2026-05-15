from pathlib import Path
import colorlog
import zipfile
import shutil
import json

from .constants import (
    DemoRepoPaths,
    PytchZipfilePaths,
    PytchZipfileMetadataKeys,
    PytchZipfileMetadataValues,
)

logger = colorlog.getLogger(__name__)


def main(new_demo_dirname: Path, locale: str, project_zipfile: Path):
    project_root = (
        new_demo_dirname
        / DemoRepoPaths.Locales_Dir
        / locale
        / DemoRepoPaths.LocaleContent.Project_Dir
    )

    if not project_root.exists():
        raise RuntimeError(f'project directory "{project_root}" does not exist')

    shutil.rmtree(project_root)
    zip = zipfile.ZipFile(project_zipfile)
    zip.extractall(project_root)
    logger.info(f"replaced content in {project_root}")

    metadata_path = project_root / PytchZipfilePaths.Project_Metadata_File
    project_metadata = json.loads(metadata_path.read_text())
    if project_metadata[PytchZipfileMetadataKeys.Linked_Content_Ref]["kind"] != "none":
        project_metadata[PytchZipfileMetadataKeys.Linked_Content_Ref] = (
            PytchZipfileMetadataValues.LinkedContentRef.No_Linked_Content
        )
        full_json = json.dumps(project_metadata, separators=(",", ":")) + "\n"
        metadata_path.write_text(full_json)
        logger.info("forced metadata value for linked-content to none")
