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

import hashlib
import json
import sys
from collections import defaultdict

import pygit2


UUID_FILENAME = "pytch-demo-uuid.txt"


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

    output = extract_data(repo)
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Top-level extraction
# ---------------------------------------------------------------------------

def extract_data(repo: pygit2.Repository) -> dict:
    if repo.head_is_unborn:
        raise RuntimeError("Repository has no HEAD; nothing to extract.")

    # Interpretation note: the spec says "searching every commit" for
    # uuid files.  I take "every commit" to mean every commit reachable
    # from HEAD.  The spec elsewhere frames the problem in terms of
    # state "as of HEAD" and describes updates to old major versions as
    # being merged back into the main line, so UUIDs that only ever
    # appear in a never-merged branch are out of scope.
    head_ancestry = list(
        repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL)
    )

    all_uuids: set[str] = set()
    successors: dict[str, set[str]] = defaultdict(set)
    # commit.id -> {uuid: (path_within_commit_tree, normalized_subtree_hash)}
    commit_demos: dict[pygit2.Oid, dict[str, tuple[str, str]]] = {}

    for commit in head_ancestry:
        demos_here: dict[str, tuple[str, str]] = {}
        for uuid, path, demo_tree in iter_demos(repo, commit.tree):
            all_uuids.add(uuid)
            demos_here[uuid] = (path, normalized_demo_hash(repo, demo_tree))
        commit_demos[commit.id] = demos_here

        for parent in commit.parents:
            for old_uuid, new_uuid in iter_uuid_replacements(repo, parent, commit):
                successors[old_uuid].add(new_uuid)
                # No need to add old_uuid / new_uuid to all_uuids here:
                # both appear in some commit's tree within HEAD's ancestry
                # (the parent's, and this commit's, respectively) and so
                # are picked up by iter_demos in those iterations.

    chain_heads = resolve_chain_heads(all_uuids, successors)
    defining_commits = find_defining_commits(
        repo, head_ancestry, commit_demos, all_uuids
    )

    assert defining_commits.keys() == all_uuids, (
        "Internal invariant violated: not every UUID has a defining commit."
    )

    sorted_uuids = sorted(all_uuids)
    return {
        "majorVersionChainHeadRecords": [
            [u, chain_heads[u]] for u in sorted_uuids
        ],
        "majorVersionDefiningCommit": [
            [u, defining_commits[u]] for u in sorted_uuids
        ],
    }


# ---------------------------------------------------------------------------
# Locating demos within a commit tree
# ---------------------------------------------------------------------------

def iter_demos(repo: pygit2.Repository, tree: pygit2.Tree, prefix: str = ""):
    """Yield ``(uuid, demo_root_path, demo_tree)`` for every demo in ``tree``.

    Interpretation note: a demo's root is the directory containing
    ``pytch-demo-uuid.txt``.  I assume demos do not nest inside other
    demos: once a directory is identified as a demo root, recursion
    stops.  The spec describes the directory containing the uuid file
    as "the root" of the demo, implying single-level identity.
    """
    for entry in tree:
        if entry.type_str == "blob" and entry.name == UUID_FILENAME:
            uuid = repo[entry.id].data.decode("utf-8").strip()
            if not uuid:
                raise RuntimeError(
                    f"{UUID_FILENAME} at "
                    f"{prefix or '<repo root>'} contains no UUID."
                )
            yield uuid, prefix, tree
            # Don't recurse into a demo root.
            return

    for entry in tree:
        if entry.type_str == "tree":
            sub_path = f"{prefix}/{entry.name}" if prefix else entry.name
            yield from iter_demos(repo, repo[entry.id], sub_path)


# ---------------------------------------------------------------------------
# Hashing a demo subtree with ``recommended`` flag normalized away
# ---------------------------------------------------------------------------

def normalized_demo_hash(repo: pygit2.Repository, demo_tree: pygit2.Tree) -> str:
    """SHA-256 over the demo subtree, ignoring the ``recommended`` flag."""
    h = hashlib.sha256()
    _hash_tree(repo, demo_tree, h, "")
    return h.hexdigest()


def _hash_tree(repo, tree, h, prefix):
    for entry in sorted(tree, key=lambda e: e.name):
        rel = f"{prefix}/{entry.name}" if prefix else entry.name
        h.update(b"\x00P")
        h.update(rel.encode("utf-8"))
        if entry.type_str == "tree":
            _hash_tree(repo, repo[entry.id], h, rel)
        elif _is_locale_metadata_path(rel):
            # The recommended flag is ignored data, so we must read the
            # blob, normalize it, and hash the normalized bytes.
            data = _normalize_locale_metadata(rel, repo[entry.id].data)
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
    return (
        len(parts) == 3
        and parts[0] == "by-locale"
        and parts[2] == "metadata.json"
    )


def _normalize_locale_metadata(rel_path: str, data: bytes) -> bytes:
    """Remove the ``recommended`` key from a metadata.json blob.

    Any structural problem with the data is a fatal error: this file is
    expected to be a JSON object that contains the ``recommended`` key,
    so failing here loudly is preferable to silently producing a
    different hash than would be expected.
    """
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"{rel_path}: contents could not be parsed as JSON ({e})"
        ) from e
    if not isinstance(obj, dict):
        raise RuntimeError(
            f"{rel_path}: top-level JSON value is "
            f"{type(obj).__name__}, not an object."
        )
    if "recommended" not in obj:
        raise RuntimeError(
            f"{rel_path}: JSON object has no 'recommended' key."
        )
    normalized = {k: v for k, v in obj.items() if k != "recommended"}
    return json.dumps(normalized, sort_keys=True).encode("utf-8")


# ---------------------------------------------------------------------------
# Detecting UUID replacements (major-version chain links)
# ---------------------------------------------------------------------------

def iter_uuid_replacements(repo, parent_commit, child_commit):
    """Yield ``(old_uuid, new_uuid)`` for every uuid file modified in place.

    Interpretation note: rename detection is deliberately NOT used.
    The spec says we cannot distinguish "moved and bumped" from
    "deleted and a new demo created", so only a delta whose
    ``old_file.path`` and ``new_file.path`` are equal counts as a UUID
    replacement.  Delete-plus-add (with any rename heuristic) is
    silently ignored.
    """
    diff = repo.diff(parent_commit, child_commit)
    for delta in diff.deltas:
        if delta.status != pygit2.GIT_DELTA_MODIFIED:
            continue
        if delta.new_file.path != delta.old_file.path:
            continue
        if _path_basename(delta.new_file.path) != UUID_FILENAME:
            continue
        old_uuid = repo[delta.old_file.id].data.decode("utf-8").strip()
        new_uuid = repo[delta.new_file.id].data.decode("utf-8").strip()
        if not old_uuid:
            raise RuntimeError(
                f"{delta.old_file.path} is empty in commit "
                f"{parent_commit.id}."
            )
        if not new_uuid:
            raise RuntimeError(
                f"{delta.new_file.path} is empty in commit "
                f"{child_commit.id}."
            )
        # old_uuid == new_uuid is benign (e.g. a mode-only delta), so
        # we filter rather than raise.
        if old_uuid != new_uuid:
            yield old_uuid, new_uuid


def _path_basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Resolving the head of each major-version chain
# ---------------------------------------------------------------------------

def resolve_chain_heads(all_uuids, successors):
    """Map every UUID to the head of its replacement chain.

    Interpretation note: if a single UUID has been replaced (on different
    commits / branches) by two *different* successor UUIDs we treat that
    as malformed history and raise.  Multiple commits replacing the same
    old UUID with the *same* new UUID (the situation described in the
    spec's example-1 setup) collapse to a single edge and are fine.
    """
    heads: dict[str, str] = {}
    for start in all_uuids:
        visited: list[str] = []
        current = start
        while True:
            if current in visited:
                raise ValueError(
                    f"Cycle in major-version chain starting at {start}: "
                    f"{visited + [current]}"
                )
            visited.append(current)
            nexts = successors.get(current, set())
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


# ---------------------------------------------------------------------------
# Finding the defining commit of each demo-major-version
# ---------------------------------------------------------------------------

def find_defining_commits(repo, head_ancestry, commit_demos, all_uuids):
    """Find the unique most-recent modification commit for every UUID."""
    mod_commits: dict[str, list[pygit2.Commit]] = defaultdict(list)
    for commit in head_ancestry:
        demos_here = commit_demos[commit.id]
        for uuid, (_path, h) in demos_here.items():
            if _is_modification(commit, uuid, h, commit_demos):
                mod_commits[uuid].append(commit)

    defining: dict[str, str] = {}
    for uuid in all_uuids:
        candidates = mod_commits.get(uuid, [])
        # Every UUID in all_uuids appears in some commit's tree within
        # HEAD's ancestry (collected by iter_demos), and the commit that
        # first introduces it is necessarily a modification commit, so
        # mod_commits[uuid] is non-empty.
        assert candidates, (
            f"Internal invariant violated: no modification commit found "
            f"for UUID {uuid}."
        )
        maxima = _topological_maxima(repo, candidates)
        if len(maxima) == 1:
            defining[uuid] = str(maxima[0].id)
            continue

        # Multiple incomparable maxima.
        #
        # Interpretation note: the spec says the *state* must be
        # well-defined.  If the normalized content of the demo agrees
        # across all maxima then only the choice of *SHA1* is ambiguous;
        # I treat that as well-defined and pick deterministically (latest
        # commit_time, then lexicographically largest SHA1).  If the
        # content itself disagrees the state really is ambiguous and we
        # raise, as the spec instructs.
        hashes = {commit_demos[m.id][uuid][1] for m in maxima}
        if len(hashes) > 1:
            sha_list = ", ".join(str(m.id) for m in maxima)
            raise RuntimeError(
                f"Ambiguous most-recent state for demo-major-version "
                f"{uuid}: incomparable modification commits {sha_list} "
                f"have differing content.  See old-versions.md for how "
                f"to disambiguate."
            )
        maxima.sort(key=lambda c: (c.commit_time, str(c.id)), reverse=True)
        defining[uuid] = str(maxima[0].id)

    return defining


def _is_modification(commit, uuid, h, commit_demos) -> bool:
    """True if ``commit`` introduced the current state of ``uuid``.

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
    if not commit.parents:
        return True
    for parent in commit.parents:
        # Invariant: every parent of a HEAD-ancestor is itself a
        # HEAD-ancestor, so parent.id is always a key of commit_demos.
        parent_demos = commit_demos[parent.id]
        parent_entry = parent_demos.get(uuid)
        if parent_entry is not None and parent_entry[1] == h:
            return False
    return True


def _topological_maxima(repo, commits):
    """Return commits with no strict descendant within the same set."""
    ids = [c.id for c in commits]
    maxima = []
    for c in commits:
        is_max = not any(
            other != c.id and repo.descendant_of(other, c.id) for other in ids
        )
        if is_max:
            maxima.append(c)
    return maxima


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
