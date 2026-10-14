import os
from pathlib import Path
import shutil
import uuid
import colorlog

from .constants import DemoRepoPaths

logger = colorlog.getLogger(__name__)


def _links_into(demo_dir: Path, target_root: Path) -> list[Path]:
    """Symlinks in `demo_dir`, outside `target_root`, pointing into it.

    A link's own target is what matters, not where any chain of links
    ends up: if the link points at a file within `target_root`, it is
    left broken once `target_root` is removed.
    """
    links: list[Path] = []
    for dir_str, dir_names, file_names in os.walk(demo_dir):
        dir_path = Path(dir_str)
        if dir_path == target_root:
            dir_names.clear()
            continue
        for name in dir_names + file_names:
            path = dir_path / name
            if not path.is_symlink():
                continue
            target = Path(os.path.normpath(dir_path / os.readlink(path)))
            if target.is_relative_to(target_root):
                links.append(path)
    return sorted(links)


def main(demo_dir: Path, locale: str) -> None:
    """Remove locale `locale` of a demo, in a new major version.

    A project linked to the demo in `locale` still needs that locale's
    content, so removing it requires a new uuid; the old uuid's
    content, including `locale`, is then still served.  The removal
    and the new uuid must be committed together.
    """
    uuid_path = demo_dir / DemoRepoPaths.Uuid_File
    if not uuid_path.is_file():
        raise RuntimeError(
            f'"{demo_dir}" is not a demo (no "{DemoRepoPaths.Uuid_File}")'
        )

    locales_dir = demo_dir / DemoRepoPaths.Locales_Dir
    locale_root = locales_dir / locale

    if locale_root.is_symlink() or not locale_root.is_dir():
        raise RuntimeError(f'"{locale_root}" is not a directory')

    other_locales = [p for p in locales_dir.iterdir() if p != locale_root]
    if not other_locales:
        raise RuntimeError(f'"{locale}" is the only locale of "{demo_dir}"')

    dependent_links = _links_into(demo_dir, locale_root)
    if dependent_links:
        links_str = ", ".join(f'"{p}"' for p in dependent_links)
        raise RuntimeError(
            f'symlinks point into "{locale_root}"; replace them with'
            f" real files first: {links_str}"
        )

    old_uuid = uuid_path.read_text().strip()
    new_uuid = str(uuid.uuid4())

    shutil.rmtree(locale_root)
    uuid_path.write_text(f"{new_uuid}\n")

    logger.info(f'Removed "{locale_root}"')
    logger.info(f"Replaced uuid {old_uuid} with {new_uuid}")
    logger.info("Commit both changes together")
