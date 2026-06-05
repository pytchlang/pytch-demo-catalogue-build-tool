import mimetypes
from pathlib import Path

mimetypes.init()


def _extensions_for_major_type(mime_major_type: str) -> list[str]:
    return [
        ext
        for ext, mimetype in mimetypes.types_map.items()
        if mimetype.startswith(f"{mime_major_type}/")
    ]


image_extensions = _extensions_for_major_type("image")
video_extensions = _extensions_for_major_type("video")


class DemoRepoPaths:
    # File within this repo containing the UUID of a demo.  As well as
    # the contents giving the UUID, the presence of this file marks the
    # directory as containing a demo.
    Uuid_File = Path("pytch-demo-uuid.txt")

    # Demo-global metadata, stored directly at top level within the
    # demo's directory.
    Global_Metadata_File = Path("metadata.json")

    # Subdirectory containing one subdirectory per locale.
    Locales_Dir = Path("by-locale")

    class LocaleContent:
        # The thumbnail image and video both have this stem, with the
        # extension determining whether image or video.
        Thumbnail_Stem = "thumbnail"

        # Metadata.
        Metadata_File = Path("metadata.json")

        # Content.
        Content_Dir = Path("content")

        # Short (summary) markdown.
        Summary_File = Path("summary.md")

        # Long description markdown.
        Description_File = Path("description.md")

        # Extracted Pytch Zipfile contents.
        Project_Dir = Path("project")


class DemoMetadataKeys:
    Author_Name = "authorName"
    Demo_Kind = "demoKind"
    Is_Recommended = "recommended"


class DistPaths:
    """
    Paths for files and directories to be written within the `dist/`
    directory.
    """

    # Top-level directory containing the by-locale index JSON files.
    Index_Dir = Path("index")

    # File within the by-locale subdirectory of the index directory.
    Index_File = Path("demos.json")

    class LocaleContent:
        # Metadata.
        Metadata_File = DemoRepoPaths.LocaleContent.Metadata_File

        # Content.
        Content_Dir = DemoRepoPaths.LocaleContent.Content_Dir

        # Short (summary) markdown.
        Summary_File = DemoRepoPaths.LocaleContent.Summary_File

        # Long description markdown.
        Description_File = DemoRepoPaths.LocaleContent.Description_File

        # Project zipfile.
        Zipfile_File = Path("project.zip")


class PytchZipfilePaths:
    """
    Paths for files and directories containing information within an
    extracted Pytch zipfile.  All paths are relative to the root of the
    project tree.
    """

    # Overall metadata for the project.
    Project_Metadata_File = Path("meta.json")

    # Zipfile version information.
    Version_File = Path("version.json")

    # Project code (either structured or flat).
    Code_File = Path("code") / Path("code.json")

    # Asset information (files and metadata).
    Assets_Dir = Path("assets")

    # Asset files themselves.
    Asset_Files_Dir = Assets_Dir / Path("files")

    # Metadata for assets.
    Assets_Metadata_File = Assets_Dir / Path("metadata.json")

    # All files which are always present in a Pytch Zipfile.
    Fixed_Files = [
        Version_File,
        Project_Metadata_File,
        Code_File,
        Assets_Metadata_File,
    ]


class PytchZipfileMetadataKeys:
    """
    Keys used within top-level Pytch Zipfile project metadata.
    """

    Project_Name = "projectName"
    Linked_Content_Ref = "linkedContentRef"


class PytchZipfileMetadataValues:
    """
    Well-known values used for Pytch Zipfile project metadata entries.
    """

    class LinkedContentRef:
        No_Linked_Content = {"kind": "none"}


class PytchZipfileCodeKeys:
    """
    Keys used within Pytch Zipfile project code JSON file.
    """

    Program_Kind = "kind"


class PytchZipfileAssetMetadataKeys:
    """
    Keys used within assets metadata of a Pytch Zipfile.
    """

    Filename = "name"
