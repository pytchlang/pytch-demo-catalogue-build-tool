import os
from pathlib import Path
import colorlog

from .constants import DemoRepoPaths, PytchZipfilePaths

logger = colorlog.getLogger(__name__)


def _relative_link_text(target: Path, link: Path) -> str:
    """Text of a symlink at `link` which points at `target`.

    The text is relative to the directory holding the link, so the link
    stays valid wherever the repo is checked out.
    """
    return os.path.relpath(target, link.parent)


def main(demo_dir: Path, src_locale: str, dst_locale: str) -> None:
    """Create locale `dst_locale` of a demo as symlinks into `src_locale`.

    The directory structure of `src_locale` is recreated as real
    directories under `dst_locale`, and every other entry (file or
    symlink) becomes a relative symlink to its counterpart in
    `src_locale`.  The user can then replace with a real file any
    symlink whose content should differ between the two locales.
    """
    if not (demo_dir / DemoRepoPaths.Uuid_File).is_file():
        raise RuntimeError(
            f'"{demo_dir}" is not a demo (no "{DemoRepoPaths.Uuid_File}")'
        )

    if src_locale == dst_locale:
        raise RuntimeError("source and destination locales must differ")

    locales_dir = demo_dir / DemoRepoPaths.Locales_Dir
    src_root = locales_dir / src_locale
    dst_root = locales_dir / dst_locale

    if src_root.is_symlink() or not src_root.is_dir():
        raise RuntimeError(f'"{src_root}" is not a directory')
    if dst_root.exists() or dst_root.is_symlink():
        raise RuntimeError(f'"{dst_root}" exists')

    n_links = 0
    for src_dir_str, dir_names, file_names in os.walk(src_root):
        src_dir = Path(src_dir_str)
        dst_dir = dst_root / src_dir.relative_to(src_root)
        dst_dir.mkdir()

        # With followlinks=False (the default), os.walk() lists a
        # symlink naming a directory in dir_names but does not descend
        # into it.  Link to such a symlink just as to a file.
        link_names = file_names + [d for d in dir_names if (src_dir / d).is_symlink()]
        for name in link_names:
            link_path = dst_dir / name
            link_path.symlink_to(_relative_link_text(src_dir / name, link_path))
            n_links += 1

    logger.info(f'Created "{dst_root}" with {n_links} symlinks into "{src_root}"')
