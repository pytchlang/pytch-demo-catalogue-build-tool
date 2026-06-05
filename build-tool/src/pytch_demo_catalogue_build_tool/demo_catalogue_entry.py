from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import constants


# Use camelCase names for convenience of front end.
@dataclass
class CatalogueEntry:
    uuid: str
    displayName: str
    authorName: str
    programKind: str
    demoKind: str
    summaryMarkdown: str
    lastUpdated: str
    recommended: bool
    thumbnailImageExtension: str
    thumbnailVideoExtension: Optional[str]
    latestUuid: str

    @property
    def thumb_image_basename(self) -> Path:
        return Path(
            constants.DemoRepoPaths.LocaleContent.Thumbnail_Stem
            + self.thumbnailImageExtension
        )

    @property
    def maybe_thumb_video_basename(self) -> Path | None:
        return (
            None
            if self.thumbnailVideoExtension is None
            else Path(
                constants.DemoRepoPaths.LocaleContent.Thumbnail_Stem
                + self.thumbnailVideoExtension
            )
        )
