from pathlib import Path
import json
import uuid
import zipfile
import colorlog

from .constants import DemoRepoPaths

logger = colorlog.getLogger(__name__)


class PlaceholderMetadata:
    Global = {
        "authorName": "TODO",
        "programKind": 'TODO: "per-method" or "flat"',
        "demoKind": 'TODO: "snippet" or "demo"',
    }

    Locale = {
        "recommended": "TODO true or false",
    }


def main(demo_dir: Path, locale: str, project_zipfile: Path):
    locales_dir = demo_dir / DemoRepoPaths.Locales_Dir
    if not demo_dir.exists():
        demo_dir.mkdir(parents=True)
        with (demo_dir / DemoRepoPaths.Global_Metadata_File).open("wt") as f_meta:
            json.dump(PlaceholderMetadata.Global, f_meta, indent=2)
        with (demo_dir / DemoRepoPaths.Uuid_File).open("wt") as f_uuid:
            f_uuid.write(f"{uuid.uuid4()}\n")
        locales_dir.mkdir()
        logger.info(f"Created directory {demo_dir} with top-level contents")

    new_locale_dir = locales_dir / locale
    if new_locale_dir.exists():
        raise RuntimeError(f"directory {new_locale_dir} exists")
    new_locale_dir.mkdir()

    local_metadata_path = new_locale_dir / DemoRepoPaths.LocaleContent.Metadata_File
    with local_metadata_path.open("wt") as f_meta:
        json.dump(PlaceholderMetadata.Locale, f_meta, indent=2)

    content_dir = new_locale_dir / DemoRepoPaths.LocaleContent.Content_Dir
    content_dir.mkdir()

    description_path = content_dir / DemoRepoPaths.LocaleContent.Description_File
    with description_path.open("wt") as f_descr:
        f_descr.write("TODO long markdown description\n")

    summary_path = content_dir / DemoRepoPaths.LocaleContent.Summary_File
    with summary_path.open("wt") as f_summary:
        f_summary.write("TODO short markdown summary\n")

    project_root = new_locale_dir / DemoRepoPaths.LocaleContent.Project_Dir
    project_root.mkdir()

    zip = zipfile.ZipFile(project_zipfile)
    zip.extractall(project_root)

    logger.info(f"Created and populated directory {new_locale_dir}")
