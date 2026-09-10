#!/usr/bin/env python3
"""Build a throwaway git repo of demo history for tests.

The build tool reads its input from a git repository, so testing it
requires a repository exhibiting the situations we care about: live
demos, superseded (new-major-version) demos, deleted demos, demos
updated only in their ``recommended`` flag, moved demos, and the
non-linear histories that ``old-versions.md`` calls out as ambiguous.

We describe the history declaratively (see ``data/history.yaml``) and
synthesise it here.

The demos synthesised here are complete.  Every file ``build-dist``
needs is present.  So the resulting repository can be fed straight to
``version_data.main`` to produce a servable ``dist/`` tree, not merely
inspected by the extractor's unit tests.

Run standalone to materialise a repo (and optionally a dist) for
inspection::

    python -m pytch_demo_catalogue_build_tool.repo_builder \
        /tmp/demo-repo --dist /tmp/demo-dist

"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid5

import click
import pygit2
import yaml

# ---------------------------------------------------------------------------
# Deterministic identities and timestamps
# ---------------------------------------------------------------------------

AUTHOR_NAME = "Test Author"
AUTHOR_EMAIL = "test@example.com"

# An arbitrary fixed epoch (2023-11-14T22:13:20Z); the absolute value is
# irrelevant, only that commit times increase in listing order.
BASE_TIME = 1_700_000_000
TIME_STEP = 60

# A 1x1 PNG.  The build tool only copies thumbnail bytes verbatim, so any
# bytes suffice for its own tests; a real (if tiny) image keeps the output
# usable as a fixture for a front-end that actually renders it.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae42"
    "6082"
)

# A stand-in MP4 for demos that carry a video thumbnail.  The build tool only
# copies thumbnail bytes verbatim, so any bytes suffice for its own tests; a
# real (if tiny) clip keeps the output usable as a fixture for a front-end
# that actually plays it.  This is a 1-second 480x360 black H.264 clip
# generated with ffmpeg; regenerate it in place if a different clip is needed.
_TINY_MP4 = (Path(__file__).parent / "data" / "tiny-thumbnail.mp4").read_bytes()


# ---------------------------------------------------------------------------
# In-memory file-state model
# ---------------------------------------------------------------------------

DEMOS_ROOT = "demos"

DATA_DIR = Path(__file__).parent / "data"

# A demo's extracted Pytch project is too elaborate to synthesise
# inline, so a complete real project of each kind is kept under this
# package's data/ dir and used as a template.  Each template's code.json
# carries the marker below at the spot that, in a real demo, would
# hold project-specific code.  We substitute the demo's uuid there so
# a major-version bump (which keeps the directory but changes the
# uuid) actually changes the project content, as it would in reality.
UUID_PLACEHOLDER = b"{{uuid}}"

# The only file in a project template that gets per-demo substitution.
PROJECT_CODE_FILE = "code/code.json"


def _json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


@dataclass(frozen=True)
class Symlink:
    """A symlink, to be written into the tree in place of a regular file.

    A demo shares content between its locales by symlinking it (see
    doc/README.md), and the build tool has to follow such a link when
    writing the dist, so the test repo needs some.  ``target`` is the
    link text, interpreted as on disk: relative to the directory holding
    the link.
    """

    target: str


# Where a demo's project keeps its asset files; a locale sharing another's
# assets symlinks each file below this prefix rather than copying it.
PROJECT_ASSET_FILES_PREFIX = "assets/files/"

# What a demo with content assets keeps in each locale's content/assets/ dir.
CONTENT_ASSET_NAMES = ["diagram.png", "caption.md"]


class FileState:
    """A repo tree, modelled as a flat POSIX-path -> contents mapping.

    A file's contents are its bytes, or a :class:`Symlink` if it is a
    symlink.  A commit's operations mutate an instance (built from its
    first parent's state); :meth:`write_tree` then serialises it into a
    git tree object.
    """

    def __init__(
        self, files: Optional[dict[str, bytes | Symlink]] = None
    ) -> None:
        self._files: dict[str, bytes | Symlink] = dict(files) if files else {}

    def copy(self) -> "FileState":
        """An independent copy; git blobs are immutable so are shared."""
        return FileState(self._files)

    # -- mutation ----------------------------------------------------

    def apply_commit(self, commit: dict[str, Any], history: "History") -> None:
        """Apply a commit's operations to this state.

        Every op carries an explicit ``kind`` (``put``/``delete``/``move``)
        that selects the operation; the remaining keys are its operands.
        The commit's ``message`` is the default text written into a put
        demo's ``description.md``.
        """
        message = commit.get("message", commit["id"])
        for op in commit.get("ops", []):
            kind = op["kind"]
            if kind == "put":
                self.put_demo(
                    f"{DEMOS_ROOT}/{op['demo']}",
                    _resolve_demo_spec(op, history, message),
                )
            elif kind == "delete":
                self.remove_subtree(f"{DEMOS_ROOT}/{op['demo']}")
            elif kind == "move":
                self.move_subtree(
                    f"{DEMOS_ROOT}/{op['from']}", f"{DEMOS_ROOT}/{op['to']}"
                )
            else:
                raise ValueError(f"unrecognised op kind: {kind!r}")

    def put_demo(self, demo_root: str, spec: "DemoSpec") -> None:
        """(Re)write a complete demo under ``demo_root``.

        Any files already under ``demo_root`` are cleared first, so an
        in-place uuid change (a new major version) replaces, rather than
        accretes, files.
        """
        self.remove_subtree(demo_root)

        def put(rel: str, data: bytes) -> None:
            self._files[f"{demo_root}/{rel}"] = data

        def put_link(rel: str, target_rel: str) -> None:
            """Symlink the file `rel` at `target_rel`.

            Both are given relative to the demo root, and the link text
            is derived from them, so it is relative to the directory
            holding the link, as git (and the filesystem) require.
            """
            target = posixpath.relpath(target_rel, posixpath.dirname(rel))
            self._files[f"{demo_root}/{rel}"] = Symlink(target)

        put("pytch-demo-uuid.txt", f"{spec.uuid}\n".encode())
        put(
            "metadata.json",
            _json({"authorName": spec.author_name, "demoKind": spec.demo_kind}),
        )

        project = _load_project_template(spec.program_kind, spec.uuid)
        if spec.display_name is not None:
            meta = json.loads(project["meta.json"])
            meta["projectName"] = spec.display_name
            project["meta.json"] = _json(meta)
        for locale in spec.locales:
            base = f"by-locale/{locale}"
            put(f"{base}/metadata.json", _json({"recommended": spec.recommended}))
            # description.md is split into `#`-headed chapters (the front end
            # renders each chapter separately); its first chapter's body is
            # the commit message (see DemoSpec.description), so the built dist
            # still reveals which commit last defined the demo.
            put(f"{base}/content/description.md", spec.render_description())
            put(f"{base}/content/summary.md", spec.render_summary())
            put(f"{base}/content/thumbnail.png", _TINY_PNG)
            # Some demos also carry a video thumbnail; the build tool picks it
            # up by its `thumbnail.<video-ext>` name (see repo_files).
            if spec.has_video:
                put(f"{base}/content/thumbnail.mp4", _TINY_MP4)
            for rel, data in project.items():
                put(f"{base}/project/{rel}", data)

    def remove_subtree(self, root: str) -> None:
        prefix = f"{root}/"
        for path in [p for p in self._files if p.startswith(prefix)]:
            del self._files[path]

    def move_subtree(self, src_root: str, dst_root: str) -> None:
        src, dst = f"{src_root}/", f"{dst_root}/"
        for path in [p for p in self._files if p.startswith(src)]:
            self._files[dst + path[len(src):]] = self._files.pop(path)

    # -- serialisation ----------------------------------------------

    def write_tree(self, repo: pygit2.Repository) -> pygit2.Oid:
        """Build a git tree object for this state and return its oid."""
        nested: dict[str, Any] = {}
        for path, data in self._files.items():
            node = nested
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = data
        return self._write_tree_node(repo, nested)

    @staticmethod
    def _write_tree_node(
        repo: pygit2.Repository, node: dict[str, Any]
    ) -> pygit2.Oid:
        builder = repo.TreeBuilder()
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                builder.insert(
                    name,
                    FileState._write_tree_node(repo, value),
                    pygit2.enums.FileMode.TREE,
                )
            else:
                builder.insert(
                    name, repo.create_blob(value), pygit2.enums.FileMode.BLOB
                )
        return builder.write()


def _load_project_template(program_kind: str, uuid: str) -> dict[str, bytes]:
    """The extracted-project files for one demo, from a kind's template.

    Every file under ``data/<kind>-program/`` is included verbatim,
    except ``code/code.json`` whose ``{{uuid}}`` marker is replaced with
    the demo's uuid.  Keys are project-relative POSIX paths.
    """
    template_dir = DATA_DIR / f"{program_kind}-program"
    if not template_dir.is_dir():
        raise ValueError(
            f"no project template for programKind {program_kind!r} "
            f"(expected {template_dir})"
        )

    files: dict[str, bytes] = {}
    for path in sorted(template_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(template_dir).as_posix()
        data = path.read_bytes()
        if rel == PROJECT_CODE_FILE:
            data = data.replace(UUID_PLACEHOLDER, uuid.encode())
        files[rel] = data
    return files


# ---------------------------------------------------------------------------
# Declarative description (parsed from YAML)
# ---------------------------------------------------------------------------


@dataclass
class DemoSpec:
    """A fully-resolved demo to write into a commit's tree."""

    uuid: str
    locales: list[str]
    recommended: bool
    description: str
    summary: str
    author_name: str
    demo_kind: str
    program_kind: str
    # Number of `#`-headed chapters in description.md (always >= 1).
    chapters: int = 1
    # Whether the demo also carries a (placeholder) video thumbnail.
    has_video: bool = False
    # When set, overrides the project template's projectName (the demo's
    # displayName); otherwise the template's own value is kept.
    display_name: Optional[str] = None

    def render_description(self) -> bytes:
        """Render this demo's ``description.md`` as one or more ``#``-headed
        chapters.

        The front end breaks a description into "chapters" at each top-level
        heading (a single ``#``).  We always emit at least one chapter, and
        ``self.chapters`` selects how many (several demos deliberately use
        more than one so multi-chapter rendering is exercised).

        The first chapter's body is ``self.description`` -- i.e. the defining
        commit's message -- so the built dist still identifies which commit
        last defined the demo, and two commits that share a ``description``
        (and ``chapters``) still produce byte-identical content.

        The bodies carry a little inline markdown so a front end has
        non-trivial markup to render and round-trip.
        """
        chapters = [
            f"# Introduction\n\nThis is the **{self.description}**.\n"
        ]
        for n in range(2, self.chapters + 1):
            chapters.append(
                f"# Chapter {n}\n\n*More* about {self.summary} (part {n}).\n"
            )
        return "\n".join(chapters).encode()

    def render_summary(self) -> bytes:
        """Render this demo's ``summary.md``.

        Include some markup.
        """
        return f"A **short** and *snappy* {self.summary}\n".encode()


@dataclass
class History:
    """A parsed, alias-resolved history description."""

    uuids: dict[str, str]
    commits: list[dict[str, Any]]
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    bulk: Optional[dict[str, Any]] = None

    def uuid(self, alias_or_literal: Optional[str]) -> Optional[str]:
        """Resolve an alias to its uuid; ``None`` and literals pass through."""
        if alias_or_literal is None:
            return None
        return self.uuids.get(alias_or_literal, alias_or_literal)


def load_history(path: Path) -> History:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML is not a mapping")
    history = History(
        uuids=raw["uuids"],
        commits=raw["commits"],
        scenarios=raw["scenarios"],
        bulk=raw.get("bulk"),
    )
    if history.bulk is not None:
        history.commits.extend(_expand_bulk(history.bulk))
    return history


# A fixed namespace so bulk demo uuids are stable across runs (the dist is
# also a front-end fixture, so its uuids must not move commit to commit).
BULK_UUID_NAMESPACE = UUID("6f3b9c1e-0d2a-4e7f-9a18-2c5d4b6e8f01")


# How many demos at the start of each category are flagged recommended.
BULK_RECOMMENDED_PER_CATEGORY = 2


def bulk_demos(bulk: dict[str, Any]) -> list[dict[str, Any]]:
    """The demos a bulk section expands to, one dict each.

    Each carries its programKind, demoKind, demo-directory name, derived
    uuid, a distinct displayName, whether it is recommended (the first
    ``BULK_RECOMMENDED_PER_CATEGORY`` of every category), how many
    description chapters it has, and whether it has a video thumbnail.  The
    last two are varied across the run so the front end sees a mix of
    single- and multi-chapter demos and of demos with and without video.
    Exposed so tests can predict the generated demos."""
    demos: list[dict[str, Any]] = []
    for program_kind in bulk["programKinds"]:
        for demo_kind in bulk["demoKinds"]:
            for i in range(bulk["count"]):
                name = f"{program_kind}-{demo_kind}-{i:02d}"
                demos.append(
                    {
                        "program_kind": program_kind,
                        "demo_kind": demo_kind,
                        "name": name,
                        "uuid": str(uuid5(BULK_UUID_NAMESPACE, name)),
                        "display_name": f"{program_kind} {demo_kind} demo {i:02d}",
                        "recommended": i < BULK_RECOMMENDED_PER_CATEGORY,
                        # 1, 2, 3, 1, 2, 3, ... -> a mix of single- and
                        # multi-chapter demos, always at least one chapter.
                        "chapters": 1 + (i % 3),
                        # Every other demo carries a video thumbnail.
                        "has_video": (i % 2 == 0),
                    }
                )
    return demos


def _expand_bulk(bulk: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a ``bulk`` section into one put-commit per generated demo.

    Each demo is its own (live) commit chained onto ``bulk["after"]`` and
    advancing ``bulk["branch"]``, so they all appear in that branch's index
    -- enough volume for the front end to exercise pagination.
    """
    commits: list[dict[str, Any]] = []
    prev = bulk["after"]
    for demo in bulk_demos(bulk):
        cid = f"bulk-{demo['name']}"
        commits.append(
            {
                "id": cid,
                "parents": [prev],
                "branch": bulk["branch"],
                "message": f"Add bulk demo {demo['name']}",
                "ops": [
                    {
                        "kind": "put",
                        "demo": f"bulk/{demo['name']}",
                        "uuid": demo["uuid"],
                        "programKind": demo["program_kind"],
                        "demoKind": demo["demo_kind"],
                        "displayName": demo["display_name"],
                        "recommended": demo["recommended"],
                        "chapters": demo["chapters"],
                        "video": demo["has_video"],
                    }
                ],
            }
        )
        prev = cid
    return commits


# ---------------------------------------------------------------------------
# Building the repository
# ---------------------------------------------------------------------------


def _resolve_demo_spec(
    op: dict[str, Any], history: History, message: str
) -> DemoSpec:
    name = op["demo"]
    return DemoSpec(
        uuid=history.uuid(op["uuid"]),
        locales=op.get("locales", ["en"]),
        recommended=op.get("recommended", True),
        # Default the description to the commit message so the dist's
        # description.md identifies the defining commit; an explicit
        # `description` is used where two commits must agree on it (e.g. a
        # change to only the `recommended` flag must not alter content).
        description=op.get("description", message),
        summary=op.get("summary", f"Summary of {name}."),
        author_name=op.get("authorName", "Ada Lovelace"),
        demo_kind=op.get("demoKind", "game"),
        # Required: a missing programKind is a defect in the history YAML,
        # so let the KeyError escape rather than guessing a default.
        program_kind=op["programKind"],
        chapters=op.get("chapters", 1),
        has_video=op.get("video", False),
        display_name=op.get("displayName"),
    )


@dataclass
class BuiltRepo:
    """A freshly built test repository and a way back to its commits."""

    path: Path
    repo: pygit2.Repository
    # Maps each declared commit id to its resulting SHA1 (hex), so tests
    # can assert which commit the extractor picked as "defining" a demo.
    commit_oids: dict[str, str]


def build_repo(repo_path: Path, history: History) -> BuiltRepo:
    """Create a git repo at ``repo_path`` realising ``history``.

    Each commit in ``history.commits`` is built in listing order.  A
    commit's ``parents`` is a list of previously-defined commit ids; it
    defaults to the immediately preceding commit (linear history), or to
    no parents for the first commit.  The commit's tree starts from its
    first parent's file state and then has the commit's ``ops`` applied.
    An optional ``branch`` updates ``refs/heads/<branch>`` to the commit.
    """
    repo = pygit2.init_repository(str(repo_path), bare=False)

    states: dict[str, FileState] = {}
    oids: dict[str, pygit2.Oid] = {}
    prev_id: Optional[str] = None

    for index, commit in enumerate(history.commits):
        cid = commit["id"]
        parent_ids = commit.get(
            "parents", [prev_id] if prev_id is not None else []
        )

        base_state = (
            states[parent_ids[0]].copy() if parent_ids else FileState()
        )
        base_state.apply_commit(commit, history)

        tree_oid = base_state.write_tree(repo)
        sig = pygit2.Signature(
            AUTHOR_NAME, AUTHOR_EMAIL, BASE_TIME + index * TIME_STEP, 0
        )
        oid = repo.create_commit(
            None,
            sig,
            sig,
            commit.get("message", cid),
            tree_oid,
            [oids[p] for p in parent_ids],
        )

        states[cid] = base_state
        oids[cid] = oid
        if "branch" in commit:
            repo.references.create(
                f"refs/heads/{commit['branch']}", oid, force=True
            )
        prev_id = cid

    return BuiltRepo(
        path=repo_path,
        repo=repo,
        commit_oids={cid: str(oid) for cid, oid in oids.items()},
    )


# ---------------------------------------------------------------------------
# Standalone entry point (handy for eyeballing the generated data)
# ---------------------------------------------------------------------------


DEFAULT_HISTORY = DATA_DIR / "history.yaml"


@click.command(help="Materialise the test demo-history repo (and optionally a dist).")
@click.argument("repo_path", type=click.Path(path_type=Path))
@click.option(
    "--history",
    "history_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=DEFAULT_HISTORY,
    show_default=True,
    help="history YAML",
)
@click.option(
    "--dist",
    type=click.Path(path_type=Path),
    default=None,
    help="also build a dist/ here",
)
@click.option(
    "--start-ref", default=None, help="branch to build the dist from"
)
def main(
    repo_path: Path,
    history_path: Path,
    dist: Optional[Path],
    start_ref: Optional[str],
) -> None:
    history = load_history(history_path)
    build_repo(repo_path, history)
    click.echo(f"built repo at {repo_path}")

    if dist is not None:
        # Imported lazily so merely building the repo does not pull in the
        # full extraction/serialisation machinery.
        from . import version_data

        version_data.main(repo_path, dist, start_ref)
        click.echo(f"built dist at {dist}")


if __name__ == "__main__":
    main()
