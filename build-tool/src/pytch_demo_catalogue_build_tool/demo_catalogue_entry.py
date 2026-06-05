from dataclasses import dataclass
from typing import Optional


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
