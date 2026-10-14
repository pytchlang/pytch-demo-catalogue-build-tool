#!/usr/bin/env python3
"""Extract demo-major-version data from a git repository.

Implements the specification in ``old-versions.md``.

Usage::

    python demo_version_data.py [REPO_PATH]

If REPO_PATH is omitted the current directory is used.  The output is a
JSON object written to stdout with two properties:

* ``majorVersionChainHeadRecords``
* ``majorVersionDefiningCommit``

Each is a list of ``[uuid, value]`` pairs as described in the spec.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Generator, Optional

import pygit2
import colorlog

from .constants import DemoCatalogueRepoPaths, DemoRepoPaths, DistPaths
from .demo_catalogue_entry import CatalogueEntry, InLangCatalogueEntry, IndexRecord
from .repo_files import entry_is_symlink, name_of_tree_entry, tree_entry_within_commit
from .demo_major_version_record import DemoMajorVersionRecord
from .validate_catalogue import main as validate_catalogue_main

UUID_FILENAME = "pytch-demo-uuid.txt"
DEMOS_DIR_PREFIX = f"{DemoCatalogueRepoPaths.Demos_Dir.as_posix()}/"


# ---------------------------------------------------------------------------
# Internal record types
# ---------------------------------------------------------------------------


@dataclass
class FoundDemo:
    """A demo discovered while walking a commit's tree."""

    uuid: str
    uuid_file_path: str
    demo_tree: pygit2.Tree


@dataclass
class DemoSnapshot:
    """Per-(commit, uuid) record: where the demo lives and its content hash."""

    uuid_file_path: str
    normalized_hash: str
    full_hash: str
    # Per locale: its normalized hash (see Extractor._locale_hashes).
    locale_normalized_hashes: dict[str, str]
    locales: frozenset[str]


@dataclass
class DefiningCommit:
    """The commit chosen as defining the most recent state of a demo."""

    sha1: str
    uuid_file_path: str


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class Extractor:
    """Walks a repository's HEAD ancestry and produces demo-major-version data.

    Construction performs the full analysis; :meth:`results` then
    returns the JSON-shaped dict described in ``old-versions.md``.  An
    instance is intended to be used once.
    """

    def __init__(
        self, repo: pygit2.Repository, start_ref: Optional[str] = None
    ) -> None:
        self.repo = repo

        # The analysis runs over the ancestry of a single "tip" commit and
        # treats that commit's tree as defining what is currently live.
        tip_commit: pygit2.Commit = self._resolve_tip(start_ref)

        # Interpretation note: the spec says "searching every commit" for
        # uuid files.  I take "every commit" to mean every commit reachable
        # from the tip.  The spec elsewhere frames the problem in terms of
        # state "as of HEAD" and describes updates to old major versions as
        # being merged back into the main line, so UUIDs that only ever
        # appear in a never-merged branch are out of scope.
        self.head_ancestry: list[pygit2.Commit] = list(
            repo.walk(tip_commit.id, pygit2.enums.SortMode.TOPOLOGICAL)
        )

        self.all_uuids: set[str] = set()
        self.successors: dict[str, set[str]] = defaultdict(set)
        # commit.id -> {uuid: DemoSnapshot}
        self.commit_demos: dict[pygit2.Oid, dict[str, DemoSnapshot]] = {}

        for commit in self.head_ancestry:
            self._scan_commit(commit)

        # A demo-major-version is "current" / discoverable iff its UUID is
        # present in the tip commit's own tree.
        self.head_uuids: set[str] = set(self.commit_demos[tip_commit.id].keys())

        self.chain_heads: dict[str, str] = self._resolve_chain_heads()

        defining_mods = self._find_latest_modifications(
            lambda demos: {uuid: s.full_hash for uuid, s in demos.items()}
        )

        if defining_mods.keys() != self.all_uuids:
            raise AssertionError("not every UUID has a defining commit")

        self.defining_commits: dict[str, DefiningCommit] = {
            uuid: DefiningCommit(
                sha1=str(commit.id),
                uuid_file_path=self.commit_demos[commit.id][uuid].uuid_file_path,
            )
            for uuid, commit in defining_mods.items()
        }

        self._check_no_locale_removed()

        mtime_mods = self._find_latest_modifications(
            lambda demos: {uuid: s.normalized_hash for uuid, s in demos.items()}
        )

        if mtime_mods.keys() != self.all_uuids:
            raise AssertionError("not every UUID has an mtime commit")

        self.effective_mtime_commits: dict[str, str] = {
            uuid: str(commit.id)
            for uuid, commit in mtime_mods.items()
        }

    def _resolve_tip(self, start_ref: Optional[str]) -> pygit2.Commit:
        """Resolve the commit whose ancestry is analysed.

        ``None`` means HEAD; otherwise ``start_ref`` is any revision
        ``revparse_single`` understands (branch, tag, or SHA1).  The
        result is peeled to a commit so an annotated tag works too.
        """
        if start_ref is None:
            if self.repo.head_is_unborn:
                raise RuntimeError("Repository has no HEAD; nothing to extract.")
            obj: pygit2.Object = self.repo[self.repo.head.target]
        else:
            obj = self.repo.revparse_single(start_ref)
        return obj.peel(pygit2.Commit)

    # ---------------------------------------------------------------
    # Formatting
    # ---------------------------------------------------------------

    def results(self) -> dict[str, Any]:
        sorted_uuids = sorted(self.all_uuids)
        return {
            "majorVersionChainHeadRecords": [
                [u, self.chain_heads[u]] for u in sorted_uuids
            ],
            "majorVersionDefiningCommit": [
                [u, (dc := self.defining_commits[u]).sha1, dc.uuid_file_path]
                for u in sorted_uuids
            ],
        }

    def demo_major_version_records(self) -> list[DemoMajorVersionRecord]:
        sorted_uuids = sorted(self.all_uuids)
        return [
            DemoMajorVersionRecord(
                self.repo,
                uuid,
                self._latest_uuid(uuid),
                (demo_commit := self.defining_commits[uuid]).sha1,
                Path(demo_commit.uuid_file_path).parent,
                self.effective_mtime_commits[uuid],
                uuid in self.head_uuids,
            )
            for uuid in sorted_uuids
        ]

    def _latest_uuid(self, uuid: str) -> str | None:
        """The live major-version this demo should point users to.

        The head of the chain is the most recent major version.  If that
        head is still present in HEAD's tree it is the live version (and a
        live demo points to itself).  If the head is absent, the whole
        chain has been deleted and there is no version to upgrade to, so
        the latest uuid is None (null when exported).
        """
        head = self.chain_heads[uuid]
        return head if head in self.head_uuids else None

    # ---------------------------------------------------------------
    # Per-commit ingestion
    # ---------------------------------------------------------------

    def _scan_commit(self, commit: pygit2.Commit) -> None:
        demos_here: dict[str, DemoSnapshot] = {}
        for found in self._iter_demos_in_commit(commit):
            self.all_uuids.add(found.uuid)
            demos_here[found.uuid] = DemoSnapshot(
                uuid_file_path=found.uuid_file_path,
                normalized_hash=self._demo_hash(
                    commit,
                    found,
                    True,
                ),
                full_hash=self._demo_hash(
                    commit,
                    found,
                    False,
                ),
                locale_normalized_hashes=self._locale_hashes(commit, found),
                locales=_locale_codes(found.demo_tree),
            )
        self.commit_demos[commit.id] = demos_here

        for parent in commit.parents:
            for old_uuid, new_uuid in self._iter_uuid_replacements(parent, commit):
                self.successors[old_uuid].add(new_uuid)
                # No need to add old_uuid / new_uuid to all_uuids here:
                # both appear in some commit's tree within HEAD's ancestry
                # (the parent's, and this commit's, respectively) and so
                # are picked up by _iter_demos in those iterations.

    def _read_uuid_blob(self, oid: pygit2.Oid, where: str) -> str:
        """Read a uuid blob, returning its stripped contents.

        Raises ``RuntimeError`` (with ``where`` for context) if the file
        is empty or whitespace-only.
        """
        obj = self.repo[oid]
        if obj.type_str != "blob":
            raise RuntimeError(f"object {oid} is not blob")
        blob: pygit2.Blob = obj  # type: ignore
        uuid = blob.data.decode("utf-8").strip()
        if not uuid:
            raise RuntimeError(f"{where} contains no UUID.")
        return uuid

    # ---------------------------------------------------------------
    # Locating demos within a commit tree
    # ---------------------------------------------------------------

    def _iter_demos_in_commit(
        self, commit: pygit2.Commit
    ) -> Generator[FoundDemo]:
        """Yield a :class:`FoundDemo` for every demo in ``commit``.

        Only the ``demos/`` directory at the top level of the repo is
        searched; a uuid file anywhere else is ignored.  A commit with
        no ``demos/`` directory has no demos.
        """
        demos_dir_name = DemoCatalogueRepoPaths.Demos_Dir.as_posix()
        try:
            demos_entry = commit.tree / demos_dir_name
        except KeyError:
            return
        if demos_entry.type_str != "tree":
            raise RuntimeError(
                f'"{demos_dir_name}" is not a tree within tree of'
                f" commit {commit.id}"
            )
        demos_tree: pygit2.Tree = demos_entry  # type: ignore
        yield from self._iter_demos(commit, demos_tree, DEMOS_DIR_PREFIX)

    def _iter_demos(
        self,
        commit: pygit2.Commit,
        tree: pygit2.Tree,
        prefix: str = "",
    ) -> Generator[FoundDemo]:
        """Yield a :class:`FoundDemo` for every demo in ``tree``.

        ``commit`` is threaded through purely for error-message context;
        ``tree`` is expected to be reachable from ``commit.tree``.

        Interpretation note: a demo's root is the directory containing
        ``pytch-demo-uuid.txt``.  I assume demos do not nest inside other
        demos: once a directory is identified as a demo root, recursion
        stops.  The spec describes the directory containing the uuid file
        as "the root" of the demo, implying single-level identity.
        """
        for entry in tree:
            if entry.type_str == "blob" and entry.name == UUID_FILENAME:
                uuid_file_path = f"{prefix}{UUID_FILENAME}"
                uuid = self._read_uuid_blob(
                    entry.id,
                    f"{uuid_file_path} in commit {commit.id}",
                )
                yield FoundDemo(
                    uuid=uuid,
                    uuid_file_path=uuid_file_path,
                    demo_tree=tree,
                )
                # Don't recurse into a demo root.
                return

        for entry in tree:
            if entry.type_str == "tree":
                entry_tree: pygit2.Tree = entry  # type: ignore
                sub_path = f"{prefix}{entry.name}/"
                yield from self._iter_demos(commit, entry_tree, sub_path)

    # ---------------------------------------------------------------
    # Hashing a demo subtree, optionally with the ``recommended`` flag
    # stripped
    # ---------------------------------------------------------------

    def _demo_hash(
            self,
            commit: pygit2.Commit,
            found: FoundDemo,
            normalise_locale_metadata: bool,
    ) -> str:
        """SHA-256 over the demo subtree, optionally ignoring the
        ``recommended`` flag."""
        h = hashlib.sha256()
        self._hash_tree(
            commit,
            Path(found.uuid_file_path).parent,
            found.demo_tree,
            h,
            "",
            normalise_locale_metadata,
        )
        return h.hexdigest()

    def _locale_hashes(
            self,
            commit: pygit2.Commit,
            found: FoundDemo,
    ) -> dict[str, str]:
        """SHA-256 for each locale of the demo, ignoring the
        ``recommended`` flag.

        A locale's hash covers its own directory within ``by-locale``
        together with everything shared by all locales, i.e.,
        everything else in the demo.
        """
        demo_root = Path(found.uuid_file_path).parent
        locales_dir_name = DemoRepoPaths.Locales_Dir.as_posix()

        shared = hashlib.sha256()
        locale_entries: list[tuple[str, pygit2.Object]] = []
        for entry in sorted(found.demo_tree, key=name_of_tree_entry):
            name = name_of_tree_entry(entry)
            if name == locales_dir_name and entry.type_str == "tree":
                locales_tree: pygit2.Tree = entry  # type: ignore
                for sub_entry in sorted(locales_tree, key=name_of_tree_entry):
                    rel = f"{name}/{name_of_tree_entry(sub_entry)}"
                    if sub_entry.type_str == "tree":
                        locale_entries.append((rel, sub_entry))
                    else:
                        self._hash_entry(
                            commit, demo_root, sub_entry, rel, shared, True
                        )
            else:
                self._hash_entry(commit, demo_root, entry, name, shared, True)

        hashes: dict[str, str] = {}
        for rel, entry in locale_entries:
            h = shared.copy()
            self._hash_entry(commit, demo_root, entry, rel, h, True)
            hashes[_path_basename(rel)] = h.hexdigest()
        return hashes

    def _hash_tree(
        self,
        commit: pygit2.Commit,
        demo_root: Path,
        tree: pygit2.Tree,
        h: hashlib._Hash,  # type: ignore[reportPrivateUsage]
        prefix: str,
        normalise_locale_metadata: bool,
    ):
        for entry in sorted(tree, key=name_of_tree_entry):
            rel = f"{prefix}/{entry.name}" if prefix else name_of_tree_entry(entry)
            self._hash_entry(
                commit, demo_root, entry, rel, h, normalise_locale_metadata
            )

    def _hash_entry(
        self,
        commit: pygit2.Commit,
        demo_root: Path,
        entry: pygit2.Object,
        rel: str,
        h: hashlib._Hash,  # type: ignore[reportPrivateUsage]
        normalise_locale_metadata: bool,
    ):
        """Add to ``h`` one entry, at path ``rel`` within the demo."""
        h.update(b"\x00P")
        h.update(rel.encode("utf-8"))
        if entry.type_str == "tree":
            subtree: pygit2.Tree = entry  # type: ignore
            self._hash_tree(
                commit, demo_root, subtree, h, rel, normalise_locale_metadata
            )
        elif _is_locale_metadata_path(rel) and normalise_locale_metadata:
            if entry.type_str != "blob":
                raise RuntimeError(f'metadata tree entry "{rel}" is not blob')

            blob: pygit2.Blob = entry  # type: ignore
            if entry_is_symlink(entry):
                # A locale sharing another's metadata (as made by
                # copy-demo-locale) has a symlink here, whose blob is
                # the link text; normalize the file it points at.
                blob = tree_entry_within_commit(  # type: ignore
                    self.repo, str(commit.id), demo_root / rel, "blob"
                )

            # The "recommended" flag is ignored data, so we must read
            # the blob, normalize it, and hash the normalized bytes.
            data = _normalize_locale_metadata(rel, blob.data)
            h.update(b"\x00B")
            h.update(data)
        else:
            # A git blob's id is SHA-1 of ``"blob <len>\x00" + content``
            # and therefore uniquely fingerprints the content, so we
            # hash the id directly and spare a blob read from the
            # object database.  Using a fixed-width id (20 bytes) also
            # rules out the encoding-ambiguity collisions that the
            # variable-length ``\x00B<data>`` form is otherwise prone
            # to.
            target: pygit2.Object = entry
            if entry_is_symlink(entry):
                # What is served is the file a symlink points at, so
                # hash that, not the link text.  Otherwise a change
                # to the target (e.g., another locale's copy of a
                # shared file, or a file outside the demo) would go
                # unnoticed.
                target = tree_entry_within_commit(
                    self.repo, str(commit.id), demo_root / rel, "blob"
                )
            h.update(b"\x00I")
            h.update(target.id.raw)

    # ---------------------------------------------------------------
    # Detecting UUID replacements (major-version chain links)
    # ---------------------------------------------------------------

    def _iter_uuid_replacements(
        self, parent_commit: pygit2.Commit, child_commit: pygit2.Commit
    ):
        """Yield ``(old_uuid, new_uuid)`` for every uuid file modified in place.

        Interpretation note: rename detection is deliberately NOT used.
        The spec says we cannot distinguish "moved and bumped" from
        "deleted and a new demo created", so only a delta whose
        ``old_file.path`` and ``new_file.path`` are equal counts as a UUID
        replacement.  Delete-plus-add (with any rename heuristic) is
        silently ignored.
        """
        diff = self.repo.diff(parent_commit, child_commit)
        for delta in diff.deltas:
            if delta.status != pygit2.GIT_DELTA_MODIFIED:
                continue
            if delta.new_file.path != delta.old_file.path:
                continue
            if not delta.new_file.path.startswith(DEMOS_DIR_PREFIX):
                continue
            if _path_basename(delta.new_file.path) != UUID_FILENAME:
                continue
            old_uuid = self._read_uuid_blob(
                delta.old_file.id,
                f"{delta.old_file.path} in commit {parent_commit.id}",
            )
            new_uuid = self._read_uuid_blob(
                delta.new_file.id,
                f"{delta.new_file.path} in commit {child_commit.id}",
            )
            # old_uuid == new_uuid is benign (e.g. a mode-only delta), so
            # we filter rather than raise.
            if old_uuid != new_uuid:
                yield old_uuid, new_uuid

    # ---------------------------------------------------------------
    # Resolving the head of each major-version chain
    # ---------------------------------------------------------------

    def _resolve_chain_heads(self) -> dict[str, str]:
        """Map every UUID to the head of its replacement chain.

        Interpretation note: if a single UUID has been replaced (on
        different commits / branches) by two *different* successor UUIDs
        we treat that as malformed history and raise.  Multiple commits
        replacing the same old UUID with the *same* new UUID (the
        situation described in the spec's example-1 setup) collapse to a
        single edge and are fine.
        """
        heads: dict[str, str] = {}
        for start in self.all_uuids:
            visited: list[str] = []
            current = start
            while True:
                if current in visited:
                    raise ValueError(
                        f"Cycle in major-version chain starting at {start}: "
                        f"{visited + [current]}"
                    )
                visited.append(current)
                nexts = self.successors.get(current, set())
                if not nexts:
                    heads[start] = current
                    break
                if len(nexts) > 1:
                    raise ValueError(
                        f"UUID {current} has multiple distinct successors "
                        f"in its major-version chain: {sorted(nexts)}"
                    )
                current = next(iter(nexts))
        return heads

    # ---------------------------------------------------------------
    # Finding the latest modification commit of each demo-major-version
    # ---------------------------------------------------------------

    def _find_latest_modifications[K](
            self,
            hashes_of: Callable[[dict[str, DemoSnapshot]], dict[K, str]],
    ) -> dict[K, pygit2.Commit]:
        """Find the unique most-recent modification commit for every key.

        ``hashes_of`` maps one commit's demos (a value of
        ``commit_demos``) to a hash for each key present in that commit.
        The caller chooses what a key is and what its hash covers.
        """
        key_hashes: dict[pygit2.Oid, dict[K, str]] = {
            commit.id: hashes_of(self.commit_demos[commit.id])
            for commit in self.head_ancestry
        }

        # Every key present in some commit within HEAD's ancestry gets at
        # least one entry here, because the commit which first introduces
        # it is necessarily a modification commit.
        mod_commits: dict[K, list[pygit2.Commit]] = defaultdict(list)
        for commit in self.head_ancestry:
            for key, h in key_hashes[commit.id].items():
                if self._is_modification(commit, key, h, key_hashes):
                    mod_commits[key].append(commit)

        latest: dict[K, pygit2.Commit] = {}
        for key, candidates in mod_commits.items():
            maxima = self._topological_maxima(candidates)
            if len(maxima) > 1:
                # Interpretation note: the spec says the *state* must be
                # well-defined.  If the hashed content agrees across all
                # maxima then only the choice of *SHA1* is ambiguous; I
                # treat that as well-defined and pick deterministically
                # (latest commit_time, then lexicographically largest
                # SHA1).  If the content itself disagrees the state
                # really is ambiguous and we raise, as the spec
                # instructs.
                hashes = {key_hashes[m.id][key] for m in maxima}
                if len(hashes) > 1:
                    sha_list = ", ".join(str(m.id) for m in maxima)
                    raise RuntimeError(
                        f"Ambiguous most-recent state for demo-major-version "
                        f"{key}: incomparable modification commits "
                        f"{sha_list} have differing content.  See "
                        f"old-versions.md for how to disambiguate."
                    )
                maxima.sort(key=lambda c: (c.commit_time, str(c.id)), reverse=True)
            latest[key] = maxima[0]

        return latest

    def _is_modification[K](
            self,
            commit: pygit2.Commit,
            key: K,
            h: str,
            key_hashes: dict[pygit2.Oid, dict[K, str]],
    ) -> bool:
        """True if ``commit`` updated the content identified by ``key``.

        A commit is a "modification commit" for a key if the key's hash
        in the commit (``h``) differs from every parent's hash for that
        key (including parents that don't have the key at all).  In
        particular, a merge commit that simply inherits its content from
        one of its parents is NOT a modification commit, which is what
        lets example 2 in the spec resolve unambiguously.

        A root commit (no parents) that has the key is trivially a
        modification commit.
        """
        for parent in commit.parents:
            # Invariant: every parent of a HEAD-ancestor is itself a
            # HEAD-ancestor, so parent.id is always a key of key_hashes.
            if key_hashes[parent.id].get(key) == h:
                return False
        return True

    def _topological_maxima(self, commits: list[pygit2.Commit]):
        """Return commits with no strict descendant within the same set."""
        ids = [c.id for c in commits]
        maxima: list[pygit2.Commit] = []
        for c in commits:
            is_max = not any(
                other != c.id and self.repo.descendant_of(other, c.id) for other in ids
            )
            if is_max:
                maxima.append(c)
        return maxima

    # ---------------------------------------------------------------
    # Checking that no demo-major-version loses a locale
    # ---------------------------------------------------------------

    def _check_no_locale_removed(self) -> None:
        """Raise if any demo-major-version has lost a locale.

        A project linked to a demo in some locale fetches that locale's
        explanatory content afresh each time it is opened, from the
        dist files of the demo's uuid.  Those files are written from
        the uuid's defining commit, so that commit must still have
        every locale the uuid has ever had.  Removing a locale requires
        a new uuid (i.e., a new major version), which leaves the old
        uuid's content, including the removed locale, still served.

        Interpretation note: a locale which was added and then removed
        before ever being deployed is harmless, but git history cannot
        tell us that, so such a removal is also an error.
        """
        all_locales: dict[str, set[str]] = defaultdict(set)
        for demos_here in self.commit_demos.values():
            for uuid, snapshot in demos_here.items():
                all_locales[uuid] |= snapshot.locales

        problems: list[str] = []
        for uuid in sorted(self.all_uuids):
            defining = self.defining_commits[uuid]
            defining_oid = pygit2.Oid(hex=defining.sha1)
            defined_locales = self.commit_demos[defining_oid][uuid].locales
            lost_locales = all_locales[uuid] - defined_locales
            if lost_locales:
                problems.append(
                    f"{uuid} ({defining.uuid_file_path} at {defining.sha1})"
                    f" has lost locale(s) {sorted(lost_locales)}"
                )

        if problems:
            raise RuntimeError(
                "Locale removed without a new major version"
                " (see remove-demo-locale): " + "; ".join(problems)
            )

# ---------------------------------------------------------------------------
# Stateless helpers
# ---------------------------------------------------------------------------


def _path_basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def _locale_codes(demo_tree: pygit2.Tree) -> frozenset[str]:
    """The names of the locale directories within a demo's tree."""
    try:
        locales_entry = demo_tree / DemoRepoPaths.Locales_Dir.as_posix()
    except KeyError:
        return frozenset()
    if locales_entry.type_str != "tree":
        return frozenset()
    locales_tree: pygit2.Tree = locales_entry  # type: ignore
    return frozenset(
        name_of_tree_entry(entry)
        for entry in locales_tree
        if entry.type_str == "tree"
    )


def _is_locale_metadata_path(rel_path: str) -> bool:
    """True for ``by-locale/<lang>/metadata.json`` within the demo root.

    Interpretation notes:

    * The spec says the recommended flag lives in
      ``by-locale/<lang>/metadata.json``.  I take this path to be
      relative to the demo root, i.e. exactly three components deep
      within the demo subtree.  ``metadata.json`` at any other depth is
      hashed verbatim (via its blob id, like any other file).

    * The spec mentions ``en`` as a "two-letter language code" example,
      but real-world locale codes are sometimes longer (e.g. ``zh-CN``).
      I therefore accept any non-empty single path component in the
      middle position rather than enforcing exactly two letters.
    """
    parts = rel_path.split("/")
    return len(parts) == 3 and parts[0] == "by-locale" and parts[2] == "metadata.json"


def _normalize_locale_metadata(rel_path: str, data: bytes) -> bytes:
    """Remove the ``recommended`` key from a metadata.json blob.

    Any structural problem with the data is a fatal error: this file is
    expected to be a JSON object that contains the ``recommended`` key,
    so failing here loudly is preferable to silently producing a
    different hash than would be expected.
    """
    obj: dict[str, Any]
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"{rel_path}: contents could not be parsed as JSON ({e})"
        ) from e
    if not isinstance(obj, dict):  # type: ignore
        raise RuntimeError(
            f"{rel_path}: top-level JSON value is "
            f"{type(obj).__name__}, not an object."
        )
    if "recommended" not in obj:
        raise RuntimeError(f"{rel_path}: JSON object has no 'recommended' key.")
    normalized = {k: v for k, v in obj.items() if k != "recommended"}
    return json.dumps(normalized, sort_keys=True).encode("utf-8")


def gather_index_records(
    dmv_records: list[DemoMajorVersionRecord],
) -> dict[str, list[CatalogueEntry]]:
    index_records: list[IndexRecord] = []
    for r in dmv_records:
        index_records.extend(r.index_contributions())

    entries_by_locale: dict[str, list[CatalogueEntry]] = defaultdict(list)
    for r in index_records:
        entries_by_locale[r[0]].append(r[1])

    for entries in entries_by_locale.values():
        entries.sort(key=lambda entry: (entry.lastUpdated, entry.uuid), reverse=True)

    return entries_by_locale


def gather_all_demos_index_entries(
    entries_by_locale: dict[str, list[CatalogueEntry]],
) -> list[InLangCatalogueEntry]:
    entries = [
        InLangCatalogueEntry(**asdict(entry), language=locale)
        for locale, locale_entries in entries_by_locale.items()
        for entry in locale_entries
    ]

    # Most recently updated first, ties broken by uuid (as for the
    # by-locale indexes); a demo's entries are then by language.
    entries.sort(key=lambda entry: entry.language)
    entries.sort(key=lambda entry: (entry.lastUpdated, entry.uuid), reverse=True)

    return entries


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(repo_path: Path, dist_path: Path, start_ref: Optional[str] = None) -> None:
    logger = colorlog.getLogger()

    discovered = pygit2.discover_repository(repo_path)
    if discovered is None:
        sys.stderr.write(f"No git repository found at {repo_path!r}\n")
        sys.exit(1)
    repo = pygit2.Repository(discovered)

    if repo.status():
        logger.warning(f"repo workdir '{repo.workdir}' contains uncommitted changes")
        for path_str in repo.status():
            logger.info(f"- {path_str}")

    records = DemoMajorVersionRecord.grouped_by_latest(
        Extractor(repo, start_ref).demo_major_version_records()
    )

    for r in records:
        r.write_dist_files(dist_path)
        logger.info(f"wrote {r.pprint_str()}")

    index_entries_by_locale = gather_index_records(records)
    for locale, index_entries in index_entries_by_locale.items():
        locale_index_dir = dist_path / DistPaths.Index_Dir / locale
        locale_index_dir.mkdir(parents=True, exist_ok=True)
        with (locale_index_dir / DistPaths.Index_File).open("wt") as f_index:
            locale_index_dicts = [asdict(entry) for entry in index_entries]
            json.dump(locale_index_dicts, f_index, indent=2)
            logger.info(f"wrote index for '{locale}'")

    all_demos_entries = gather_all_demos_index_entries(index_entries_by_locale)
    index_dir = dist_path / DistPaths.Index_Dir
    index_dir.mkdir(parents=True, exist_ok=True)
    with (index_dir / DistPaths.All_Demos_Index_File).open("wt") as f_index:
        all_demos_dicts = [asdict(entry) for entry in all_demos_entries]
        json.dump(all_demos_dicts, f_index, indent=2)
        logger.info("wrote cross-language index")

    data_dir = Path(__file__).parent / "data"
    spec_path = data_dir / "disco-demos-openapi.yaml"
    if not spec_path.is_file():
        logger.warning(
            f"OpenAPI spec file '{spec_path}' does not exist;"
            " unable to validate output"
        )
    else:
        validate_catalogue_main(spec_path, dist_path)
