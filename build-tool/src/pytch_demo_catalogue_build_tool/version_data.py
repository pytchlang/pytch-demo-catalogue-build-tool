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
from typing import Any, Generator

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass

import pygit2

UUID_FILENAME = "pytch-demo-uuid.txt"


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


@dataclass
class DefiningCommit:
    """The commit chosen as defining the most recent state of a demo."""

    sha1: str
    uuid_file_path: str


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    repo_path_arg = sys.argv[1] if len(sys.argv) > 1 else "."
    discovered = pygit2.discover_repository(repo_path_arg)
    if discovered is None:
        sys.stderr.write(f"No git repository found at {repo_path_arg!r}\n")
        sys.exit(1)
    repo = pygit2.Repository(discovered)

    output = Extractor(repo).results()
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class Extractor:
    """Walks a repository's HEAD ancestry and produces demo-major-version data.

    Construction performs the full analysis; :meth:`results` then
    returns the JSON-shaped dict described in ``old-versions.md``.  An
    instance is intended to be used once.
    """

    def __init__(self, repo: pygit2.Repository) -> None:
        if repo.head_is_unborn:
            raise RuntimeError("Repository has no HEAD; nothing to extract.")

        self.repo = repo

        # Interpretation note: the spec says "searching every commit" for
        # uuid files.  I take "every commit" to mean every commit reachable
        # from HEAD.  The spec elsewhere frames the problem in terms of
        # state "as of HEAD" and describes updates to old major versions as
        # being merged back into the main line, so UUIDs that only ever
        # appear in a never-merged branch are out of scope.
        self.head_ancestry: list[pygit2.Commit] = list(
            repo.walk(repo.head.target, pygit2.enums.SortMode.TOPOLOGICAL)
        )

        self.all_uuids: set[str] = set()
        self.successors: dict[str, set[str]] = defaultdict(set)
        # commit.id -> {uuid: DemoSnapshot}
        self.commit_demos: dict[pygit2.Oid, dict[str, DemoSnapshot]] = {}

        for commit in self.head_ancestry:
            self._scan_commit(commit)

        self.chain_heads: dict[str, str] = self._resolve_chain_heads()
        self.defining_commits: dict[str, DefiningCommit] = self._find_defining_commits()

        assert (
            self.defining_commits.keys() == self.all_uuids
        ), "Internal invariant violated: not every UUID has a defining commit."

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

    # ---------------------------------------------------------------
    # Per-commit ingestion
    # ---------------------------------------------------------------

    def _scan_commit(self, commit: pygit2.Commit) -> None:
        demos_here: dict[str, DemoSnapshot] = {}
        for found in self._iter_demos(commit, commit.tree):
            self.all_uuids.add(found.uuid)
            demos_here[found.uuid] = DemoSnapshot(
                uuid_file_path=found.uuid_file_path,
                normalized_hash=self._normalized_demo_hash(found.demo_tree),
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
    # Hashing a demo subtree with the ``recommended`` flag stripped
    # ---------------------------------------------------------------

    def _normalized_demo_hash(self, demo_tree: pygit2.Tree) -> str:
        """SHA-256 over the demo subtree, ignoring the ``recommended`` flag."""
        h = hashlib.sha256()
        self._hash_tree(demo_tree, h, "")
        return h.hexdigest()

    def _hash_tree(
        self,
        tree: pygit2.Tree,
        h: hashlib._Hash,  # type: ignore[reportPrivateUsage]
        prefix: str,
    ):
        def name_of_tree_entry(entry: pygit2.Object) -> str:
            if entry.name is None:
                raise RuntimeError(f"Object {entry.id} has no name")
            return entry.name

        for entry in sorted(tree, key=name_of_tree_entry):
            rel = f"{prefix}/{entry.name}" if prefix else name_of_tree_entry(entry)
            h.update(b"\x00P")
            h.update(rel.encode("utf-8"))
            if entry.type_str == "tree":
                subtree: pygit2.Tree = entry  # type: ignore
                self._hash_tree(subtree, h, rel)
            elif _is_locale_metadata_path(rel):
                if entry.type_str != "blob":
                    raise RuntimeError(f'metadata tree entry "{rel}" is not blob')

                blob: pygit2.Blob = entry  # type: ignore

                # The recommended flag is ignored data, so we must read
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
                h.update(b"\x00I")
                h.update(entry.id.raw)

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
    # Finding the defining commit of each demo-major-version
    # ---------------------------------------------------------------

    def _find_defining_commits(self) -> dict[str, DefiningCommit]:
        """Find the unique most-recent modification commit for every UUID."""
        mod_commits: dict[str, list[pygit2.Commit]] = defaultdict(list)
        for commit in self.head_ancestry:
            demos_here = self.commit_demos[commit.id]
            for uuid, snapshot in demos_here.items():
                if self._is_modification(commit, uuid, snapshot.normalized_hash):
                    mod_commits[uuid].append(commit)

        defining: dict[str, DefiningCommit] = {}
        for uuid in self.all_uuids:
            candidates = mod_commits.get(uuid, [])
            # Every UUID in all_uuids appears in some commit's tree within
            # HEAD's ancestry (collected by _iter_demos), and the commit
            # that first introduces it is necessarily a modification
            # commit, so mod_commits[uuid] is non-empty.
            assert candidates, (
                f"Internal invariant violated: no modification commit found "
                f"for UUID {uuid}."
            )
            maxima = self._topological_maxima(candidates)
            if len(maxima) > 1:
                # Interpretation note: the spec says the *state* must be
                # well-defined.  If the normalized content of the demo
                # agrees across all maxima then only the choice of *SHA1*
                # is ambiguous; I treat that as well-defined and pick
                # deterministically (latest commit_time, then
                # lexicographically largest SHA1).  If the content itself
                # disagrees the state really is ambiguous and we raise,
                # as the spec instructs.
                hashes = {self.commit_demos[m.id][uuid].normalized_hash for m in maxima}
                if len(hashes) > 1:
                    sha_list = ", ".join(str(m.id) for m in maxima)
                    raise RuntimeError(
                        f"Ambiguous most-recent state for demo-major-version "
                        f"{uuid}: incomparable modification commits "
                        f"{sha_list} have differing content.  See "
                        f"old-versions.md for how to disambiguate."
                    )
                maxima.sort(key=lambda c: (c.commit_time, str(c.id)), reverse=True)
            chosen = maxima[0]
            defining[uuid] = DefiningCommit(
                sha1=str(chosen.id),
                uuid_file_path=self.commit_demos[chosen.id][uuid].uuid_file_path,
            )

        return defining

    def _is_modification(self, commit: pygit2.Commit, uuid: str, h: str) -> bool:
        """True if ``commit`` updated the contents of demo ``uuid``.

        A commit is a "modification commit" for a demo if the demo exists
        in the commit's tree and the demo's (normalized) content differs
        from every parent's version of that same demo (including parents
        that don't have the demo at all).  In particular, a merge commit
        that simply inherits its demo content from one of its parents is
        NOT a modification commit, which is what lets example 2 in the
        spec resolve unambiguously.

        A root commit (no parents) that contains the demo is trivially a
        modification commit.
        """
        for parent in commit.parents:
            # Invariant: every parent of a HEAD-ancestor is itself a
            # HEAD-ancestor, so parent.id is always a key of commit_demos.
            parent_demos = self.commit_demos[parent.id]
            parent_snapshot = parent_demos.get(uuid)
            if parent_snapshot is not None and parent_snapshot.normalized_hash == h:
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


# ---------------------------------------------------------------------------
# Stateless helpers
# ---------------------------------------------------------------------------


def _path_basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


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


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
