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
