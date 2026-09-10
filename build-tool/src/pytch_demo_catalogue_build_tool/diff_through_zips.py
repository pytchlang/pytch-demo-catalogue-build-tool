#!/usr/bin/env python3

"""Recursively diff two file trees, looking *inside* zipfiles.

Behaves like

    diff -Naur OLD NEW

(recursive; missing files treated as empty; unified output), except
that when a file is a zipfile on both sides, its *contents* are
compared entry by entry rather than the zipfile bytes being compared.
This makes the output insensitive to the ways in which zipfiles fail
to be bitwise reproducible: entry timestamps, entry order, compression
choices, and so on.

Paths within a zipfile are shown with a "!/" separator, in the style
of jar URLs:

    dist/e9fb...f6/en/project.zip!/code/code.json

Zipfiles nested within zipfiles are handled the same way, up to
--max-zip-depth.

Exit status follows diff(1): 0 if the trees are the same, 1 if they
differ, 2 if something went wrong.
"""

from __future__ import annotations

import argparse
import difflib
import io
import logging
import colorlog
import os
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional


logger = logging.getLogger("zipdiff")


# A file starting with any of these is (or claims to be) a zipfile:
# local file header, end-of-central-directory (an empty zip), and
# spanned-archive marker.
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

DEV_NULL = "/dev/null"


class Trouble(Exception):
    """Something went wrong; report and exit with diff(1)'s status 2."""


@dataclass
class Node:
    """One entry in a tree: a file, a symlink, a directory, or an oddity.

    For a "file", `read()` gives its contents as bytes.  For a
    "symlink", `target` is the raw (unresolved) link text.
    """

    kind: str  # "file" | "symlink" | "dir" | "other"
    read: Optional[Callable[[], bytes]] = None
    target: Optional[str] = None

    def description(self) -> str:
        return {
            "file": "a regular file",
            "symlink": "a symbolic link",
            "dir": "a directory",
            "other": "neither a regular file nor a directory",
        }[self.kind]


# A tree maps a relative path (always "/"-separated, never with a
# trailing slash) to the Node found there.  Every ancestor directory of
# an entry is itself present as a "dir" node.
Tree = dict[str, Node]


########################################################################
#
# Building trees, from the filesystem and from zipfiles.


def fs_tree(root: Path, follow_symlinks: bool) -> Tree:
    """Tree of everything under the directory `root`."""

    tree: Tree = {}

    def recurse(dir_path: Path, prefix: str) -> None:
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as err:
            raise Trouble(f"cannot read directory {dir_path}: {err}") from err

        for entry in entries:
            rel_path = prefix + entry.name
            if entry.is_symlink() and not follow_symlinks:
                tree[rel_path] = Node("symlink", target=os.readlink(entry.path))
            elif entry.is_dir(follow_symlinks=follow_symlinks):
                tree[rel_path] = Node("dir")
                recurse(Path(entry.path), rel_path + "/")
            elif entry.is_file(follow_symlinks=follow_symlinks):
                tree[rel_path] = Node("file", read=fs_reader(Path(entry.path)))
            else:
                # A dangling symlink we were asked to follow, or a
                # fifo/socket/device.
                logger.warning("ignoring contents of %s (not a file)", entry.path)
                tree[rel_path] = Node("other")

    recurse(root, "")
    logger.info("read %d entries from %s", len(tree), root)
    return tree


def fs_reader(path: Path) -> Callable[[], bytes]:
    def read() -> bytes:
        try:
            return path.read_bytes()
        except OSError as err:
            raise Trouble(f"cannot read {path}: {err}") from err

    return read


def zip_tree(data: bytes, label: str) -> Tree:
    """Tree of the entries within the zipfile whose bytes are `data`.

    Only the names and contents of the entries are looked at; all other
    zipfile metadata (timestamps, compression method, entry order,
    external attributes) is deliberately ignored.
    """

    tree: Tree = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                rel_path = info.filename.replace("\\", "/").rstrip("/")
                if not rel_path or rel_path in (".", ".."):
                    continue
                for ancestor in ancestor_dirs(rel_path):
                    tree.setdefault(ancestor, Node("dir"))
                if info.is_dir():
                    tree.setdefault(rel_path, Node("dir"))
                    continue
                if rel_path in tree and tree[rel_path].kind == "file":
                    logger.warning("%s: duplicate entry %s", label, rel_path)
                tree[rel_path] = Node("file", read=zip_reader(data, info.filename))
    except (zipfile.BadZipFile, OSError) as err:
        raise Trouble(f"cannot read zipfile {label}: {err}") from err

    logger.info("read %d entries from %s", len(tree), label)
    return tree


def zip_reader(data: bytes, name: str) -> Callable[[], bytes]:
    def read() -> bytes:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.read(name)

    return read


def ancestor_dirs(rel_path: str) -> Iterator[str]:
    parts = rel_path.split("/")
    for i in range(1, len(parts)):
        yield "/".join(parts[:i])


def looks_like_zip(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in ZIP_MAGICS)


########################################################################
#
# Output.


class Printer:
    """Writes diff output, optionally coloured."""

    RESET = "\x1b[0m"
    COLOURS = {
        "meta": "\x1b[1m",  # "diff -Naur ..." and "Only in ..." lines
        "old": "\x1b[31m",  # "---" and "-" lines
        "new": "\x1b[32m",  # "+++" and "+" lines
        "hunk": "\x1b[36m",  # "@@" lines
    }

    def __init__(self, stream, colour: bool) -> None:
        self.stream = stream
        self.colour = colour

    def line(self, text: str, style: Optional[str] = None) -> None:
        if self.colour and style is not None:
            text = f"{self.COLOURS[style]}{text}{self.RESET}"
        self.stream.write(text + "\n")

    def diff_line(self, text: str) -> None:
        """Write one line of unified-diff output, coloured by its prefix."""

        if text.startswith("+++"):
            style = "new"
        elif text.startswith("---"):
            style = "old"
        elif text.startswith("@@"):
            style = "hunk"
        elif text.startswith("+"):
            style = "new"
        elif text.startswith("-"):
            style = "old"
        else:
            style = None
        self.line(text, style)


########################################################################
#
# Comparing.


@dataclass
class Comparer:
    printer: Printer
    context_lines: int
    max_zip_depth: int
    differences: int = field(default=0, init=False)

    def note_difference(self) -> None:
        self.differences += 1

    # -- Trees ---------------------------------------------------------

    def compare_trees(
        self,
        old_tree: Tree,
        new_tree: Tree,
        old_label: str,
        new_label: str,
        separator: str,
        zip_depth: int,
    ) -> None:
        """Compare two trees, whose roots are `old_label` and `new_label`.

        Child paths are labelled by joining with `separator`, which is
        "/" for a directory and "!/" for a zipfile.
        """

        for rel_path in sorted(set(old_tree) | set(new_tree)):
            self.compare_nodes(
                old_tree.get(rel_path),
                new_tree.get(rel_path),
                f"{old_label}{separator}{rel_path}",
                f"{new_label}{separator}{rel_path}",
                zip_depth,
                old_tree=old_tree,
                new_tree=new_tree,
                rel_path=rel_path,
            )

    def compare_nodes(
        self,
        old_node: Optional[Node],
        new_node: Optional[Node],
        old_label: str,
        new_label: str,
        zip_depth: int,
        old_tree: Optional[Tree] = None,
        new_tree: Optional[Tree] = None,
        rel_path: Optional[str] = None,
    ) -> None:
        if old_node is None or new_node is None:
            present, present_label, missing_label, in_old = (
                (new_node, new_label, old_label, False)
                if old_node is None
                else (old_node, old_label, new_label, True)
            )
            assert present is not None
            tree = old_tree if in_old else new_tree
            self.report_only_in(
                present, present_label, missing_label, in_old, tree, rel_path, zip_depth
            )
            return

        if old_node.kind != new_node.kind:
            self.note_difference()
            self.printer.line(
                f"File {old_label} is {old_node.description()}"
                f" while file {new_label} is {new_node.description()}",
                "meta",
            )
            return

        if old_node.kind == "dir":
            return

        if old_node.kind == "symlink":
            if old_node.target != new_node.target:
                self.note_difference()
                self.printer.line(
                    f"Symbolic links {old_label} and {new_label} differ:"
                    f" {old_node.target!r} vs {new_node.target!r}",
                    "meta",
                )
            return

        if old_node.kind == "other":
            return

        self.compare_files(old_node, new_node, old_label, new_label, zip_depth)

    def report_only_in(
        self,
        node: Node,
        present_label: str,
        missing_label: str,
        in_old: bool,
        tree: Optional[Tree],
        rel_path: Optional[str],
        zip_depth: int,
    ) -> None:
        """Handle a path which exists on one side only.

        Like `diff -N`, a missing file is treated as an empty file, so
        the whole of the present file is shown as added or deleted.  A
        missing directory needs no report of its own, since each of its
        entries is reported; the exception is an *empty* directory,
        which would otherwise vanish silently.
        """

        if node.kind == "dir":
            empty = tree is not None and rel_path is not None and not has_children(
                tree, rel_path
            )
            if empty:
                self.note_difference()
                self.printer.line(f"{only_in(present_label)} (empty directory)", "meta")
            return

        if node.kind == "other":
            self.note_difference()
            self.printer.line(f"{only_in(present_label)} ({node.description()})", "meta")
            return

        if node.kind == "symlink":
            self.note_difference()
            self.printer.line(
                f"{only_in(present_label)} (symbolic link to {node.target!r})", "meta"
            )
            return

        assert node.read is not None
        data = node.read()

        if self.should_recurse_into_zip(data, zip_depth):
            # Show the contents of the added/deleted zipfile as
            # individual added/deleted files, which is far more useful
            # than "Binary files ... differ".
            present_tree = zip_tree(data, present_label)
            empty_tree: Tree = {}
            old_tree, new_tree = (
                (present_tree, empty_tree) if in_old else (empty_tree, present_tree)
            )
            old_label, new_label = (
                (present_label, missing_label)
                if in_old
                else (missing_label, present_label)
            )
            self.compare_trees(
                old_tree, new_tree, old_label, new_label, "!/", zip_depth + 1
            )
            return

        old_data, new_data = (data, b"") if in_old else (b"", data)
        old_label, new_label = (
            (present_label, DEV_NULL) if in_old else (DEV_NULL, present_label)
        )
        self.emit_content_diff(old_data, new_data, old_label, new_label)

    # -- Files ---------------------------------------------------------

    def compare_files(
        self,
        old_node: Node,
        new_node: Node,
        old_label: str,
        new_label: str,
        zip_depth: int,
    ) -> None:
        assert old_node.read is not None and new_node.read is not None
        old_data = old_node.read()
        new_data = new_node.read()

        if old_data == new_data:
            return

        old_is_zip = self.should_recurse_into_zip(old_data, zip_depth)
        new_is_zip = self.should_recurse_into_zip(new_data, zip_depth)

        if old_is_zip and new_is_zip:
            self.compare_trees(
                zip_tree(old_data, old_label),
                zip_tree(new_data, new_label),
                old_label,
                new_label,
                "!/",
                zip_depth + 1,
            )
            return

        if old_is_zip != new_is_zip:
            zip_label = old_label if old_is_zip else new_label
            plain_label = new_label if old_is_zip else old_label
            self.note_difference()
            self.printer.line(
                f"File {zip_label} is a zipfile"
                f" while file {plain_label} is not",
                "meta",
            )
            return

        self.emit_content_diff(old_data, new_data, old_label, new_label)

    def should_recurse_into_zip(self, data: bytes, zip_depth: int) -> bool:
        if zip_depth >= self.max_zip_depth:
            return False
        if not looks_like_zip(data):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)):
                return True
        except (zipfile.BadZipFile, OSError):
            # Starts like a zipfile but isn't a usable one; fall back to
            # comparing the bytes.
            return False

    def emit_content_diff(
        self, old_data: bytes, new_data: bytes, old_label: str, new_label: str
    ) -> None:
        self.note_difference()

        old_text = as_text(old_data)
        new_text = as_text(new_data)

        if old_text is None or new_text is None:
            self.printer.line(
                f"Binary files {old_label} and {new_label} differ", "meta"
            )
            return

        lines = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
            n=self.context_lines,
        )
        for line in lines:
            if line.endswith("\n"):
                self.printer.diff_line(line[:-1])
            else:
                self.printer.diff_line(line)
                self.printer.line("\\ No newline at end of file")


def only_in(label: str) -> str:
    """A message in the style of diff(1)'s "Only in DIR: NAME"."""

    parent, _, name = label.rpartition("/")
    if not parent:
        return f"Only in .: {name}"
    if parent.endswith("!"):
        # A path directly inside a zipfile; keep the "!/" separator.
        parent += "/"
    return f"Only in {parent}: {name}"


def has_children(tree: Tree, rel_path: str) -> bool:
    prefix = rel_path + "/"
    return any(path.startswith(prefix) for path in tree)


def as_text(data: bytes) -> Optional[str]:
    """The contents as text, or None if they look binary."""

    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


########################################################################
#
# Command line.


def configure_logging(log_level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        colorlog.ColoredFormatter("%(log_color)s%(levelname)s%(reset)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, log_level))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Diff two file trees like "diff -Naur", except that zipfiles'
            " are compared by their contents rather than their bytes."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("old", type=Path, help="original file or directory")
    parser.add_argument("new", type=Path, help="modified file or directory")
    parser.add_argument(
        "-U",
        "--unified",
        type=int,
        default=3,
        metavar="N",
        dest="context_lines",
        help="number of lines of context",
    )
    parser.add_argument(
        "--color",
        "--colour",
        choices=["auto", "always", "never"],
        default="auto",
        dest="colour",
        help="colourise the output",
    )
    parser.add_argument(
        "--max-zip-depth",
        type=int,
        default=8,
        metavar="N",
        help="how deeply to descend into zipfiles within zipfiles",
    )
    parser.add_argument(
        "-L",
        "--dereference",
        action="store_true",
        help=(
            "follow symlinks, comparing what they point at; by default a"
            " symlink is compared as a symlink, so replacing one with a"
            " copy of its target is reported as a difference"
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="verbosity of progress logging (on stderr)",
    )
    return parser.parse_args(argv)


def node_for_path(path: Path, follow_symlinks: bool) -> Node:
    if path.is_symlink() and not follow_symlinks:
        return Node("symlink", target=os.readlink(path))
    if path.is_dir():
        return Node("dir")
    if path.is_file():
        return Node("file", read=fs_reader(path))
    raise Trouble(f"cannot compare {path}: not a file or directory")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)

    colour = args.colour == "always" or (
        args.colour == "auto" and sys.stdout.isatty()
    )
    printer = Printer(sys.stdout, colour)
    comparer = Comparer(
        printer=printer,
        context_lines=args.context_lines,
        max_zip_depth=args.max_zip_depth,
    )

    for path in (args.old, args.new):
        if not path.exists() and not path.is_symlink():
            raise Trouble(f"{path}: no such file or directory")

    old_label = str(args.old)
    new_label = str(args.new)
    old_node = node_for_path(args.old, args.dereference)
    new_node = node_for_path(args.new, args.dereference)

    if old_node.kind == "dir" and new_node.kind == "dir":
        comparer.compare_trees(
            fs_tree(args.old, args.dereference),
            fs_tree(args.new, args.dereference),
            old_label.rstrip("/"),
            new_label.rstrip("/"),
            "/",
            zip_depth=0,
        )
    else:
        comparer.compare_nodes(old_node, new_node, old_label, new_label, zip_depth=0)

    return 1 if comparer.differences else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Trouble as err:
        print(f"zipdiff: {err}", file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        # Output piped into something like "head".
        os._exit(2)
    except KeyboardInterrupt:
        sys.exit(2)
