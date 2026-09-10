"""Tests for the major-version extraction in ``version_data``.

Each test builds a throwaway git repository from the declarative history
in ``data/history.yaml`` (see ``repo_builder``), points the extractor at
one of the branches that history defines, and checks the result against
the expectations declared alongside the history.  The repository lives in
a temporary directory and is discarded when the session ends.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pytch_demo_catalogue_build_tool import version_data
from pytch_demo_catalogue_build_tool.version_data import Extractor
from pytch_demo_catalogue_build_tool.validate_catalogue import validate

from pytch_demo_catalogue_build_tool.repo_builder import (
    DATA_DIR,
    DEFAULT_HISTORY,
    BuiltRepo,
    History,
    build_repo,
    bulk_demos,
    load_history,
)

HISTORY_PATH = DEFAULT_HISTORY

# A symlink (assumed present) within the package's data/ dir pointing at the
# OpenAPI spec the served catalogue must conform to.
SPEC_PATH = DATA_DIR / "disco-demos-openapi.yaml"

HISTORY = load_history(HISTORY_PATH)


def _scenarios(*, with_error: bool) -> list:
    return [s for s in HISTORY.scenarios if ("expect_error" in s) == with_error]


@pytest.fixture(scope="session")
def history() -> History:
    return HISTORY


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> BuiltRepo:
    """A git repo realising the whole history, built once for the session."""
    path = tmp_path_factory.mktemp("demos") / "repo"
    return build_repo(path, HISTORY)


@pytest.fixture(scope="session")
def dist(built: BuiltRepo, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A dist/ built once from the catalogue branch, shared by build tests."""
    out = tmp_path_factory.mktemp("dist")
    version_data.main(built.path, out, "catalogue")
    return out


@pytest.fixture(scope="session")
def dist_demos_index(dist: Path):
    return json.loads((dist / "index" / "en" / "demos.json").read_text())


@pytest.mark.parametrize(
    "scenario", _scenarios(with_error=False), ids=lambda s: s["start_ref"]
)
def test_extracted_records(history: History, built: BuiltRepo, scenario: dict) -> None:
    extractor = Extractor(built.repo, scenario["start_ref"])
    records = {r.uuid: r for r in extractor.demo_major_version_records()}

    for alias, want in scenario["expect"].items():
        uuid = history.uuid(alias)
        record = records[uuid]

        want_latest = history.uuid(want["latest"])  # None passes through
        latest_err = f"{alias}: latest_uuid {record.latest_uuid} != {want_latest}"
        assert record.latest_uuid == want_latest, latest_err
        assert record.present_at_head == want["present"], alias

        if "defined_at" in want:
            want_sha = built.commit_oids[want["defined_at"]]
            got_sha = extractor.defining_commits[uuid].sha1
            assert got_sha == want_sha, (
                f"{alias}: defined_at {got_sha} != {want_sha} "
                f"({want['defined_at']})"
            )

        # The "mtime commit" is the most recent change OTHER than just
        # a "recommended" flip, so it can lag behind the "defining
        # commit" (see `live` and `beacon`).
        if "mtime_at" in want:
            want_sha = built.commit_oids[want["mtime_at"]]
            got_sha = extractor.effective_mtime_commits[uuid]
            assert got_sha == want_sha, (
                f"{alias}: mtime_at {got_sha} != {want_sha} "
                f"({want['mtime_at']})"
            )

        if "program_kind" in want:
            assert record.common_program_kind() == want["program_kind"], alias


@pytest.mark.parametrize(
    "scenario", _scenarios(with_error=True), ids=lambda s: s["start_ref"]
)
def test_ambiguous_history_raises(built: BuiltRepo, scenario: dict) -> None:
    with pytest.raises(RuntimeError, match="[Aa]mbiguous"):
        Extractor(built.repo, scenario["start_ref"])


def test_build_dist_marks_deleted_demo(
    history: History, dist: Path, dist_demos_index: list[dict]
) -> None:
    """A full build: deleted demos keep their explanatory content but get a
    null latestUuid and no fresh project zip; superseded demos point at the
    live successor; live demos point to themselves and get a zip."""

    def locale_dir(alias: str) -> Path:
        return dist / history.uuid(alias) / "en"

    def meta(alias: str) -> dict:
        return json.loads((locale_dir(alias) / "metadata.json").read_text())

    def description(alias: str) -> str:
        return (locale_dir(alias) / "content" / "description.md").read_text()

    def zip_exists(alias: str) -> bool:
        return (locale_dir(alias) / "project.zip").exists()

    # Deleted demo: explanatory content retained, latestUuid null, no zip.
    assert meta("gone")["latestUuid"] is None
    assert (locale_dir("gone") / "content" / "description.md").is_file()
    assert not zip_exists("gone")

    # Superseded demo: points at the live successor, still no fresh zip.
    assert meta("superA")["latestUuid"] == history.uuid("superB")
    assert not zip_exists("superA")

    # Live demos: self-pointer and a fresh project zip.
    assert meta("live")["latestUuid"] == history.uuid("live")
    assert zip_exists("live")

    # programKind flows through to the served entry for both kinds.
    assert meta("live")["programKind"] == "per-method"
    assert meta("superB")["programKind"] == "flat"

    # The per-method demo's zip carries the template's asset file.
    with zipfile.ZipFile(locale_dir("live") / "project.zip") as zf:
        assert any(name.startswith("assets/files/") for name in zf.namelist())

    # A description-only change moved the defining commit: the built
    # description.md's first chapter carries the rewording commit's message,
    # not the add's.
    exp_fragment = "Reword the story demo's description (a content change)"
    assert exp_fragment in description("descr")

    # The index lists exactly the live demos: the named live ones plus every
    # generated bulk demo, and none of the deleted/superseded versions.
    listed = {e["uuid"] for e in dist_demos_index}
    named_live = {
        history.uuid(a)
        for a in ("live", "superB", "mover", "descr", "beacon", "poly")
    }
    bulk_uuids = {d["uuid"] for d in bulk_demos(history.bulk)}
    assert listed == named_live | bulk_uuids
    for a in ("gone", "chainA", "chainB", "superA"):
        assert history.uuid(a) not in listed


def test_symlinked_content_is_copied_into_dist(history: History, dist: Path) -> None:
    """A demo sharing content between its locales by symlink gets real files
    in the dist, not the link targets written out as text.

    The `polyglot` demo's `ga` locale symlinks each of its content assets
    at `en`'s copy, and `en`'s `caption.md` is itself a symlink to the
    summary in the content directory above it -- so `ga`'s caption is a
    link to a link.
    """
    uuid = history.uuid("poly")

    def assets_dir(locale: str) -> Path:
        return dist / uuid / locale / "content" / "assets"

    def summary(locale: str) -> str:
        meta = json.loads((dist / uuid / locale / "metadata.json").read_text())
        return meta["summaryMarkdown"]

    # The shared image: the same bytes in both locales, really an image
    # rather than the text of a link to one, and a copy rather than a
    # link (the dist is served as plain files, and is not necessarily
    # even written to a filesystem which has symlinks).
    png_bytes = (assets_dir("en") / "diagram.png").read_bytes()
    assert png_bytes.startswith(b"\x89PNG")
    assert (assets_dir("ga") / "diagram.png").read_bytes() == png_bytes
    assert not (assets_dir("ga") / "diagram.png").is_symlink()

    # Following the chain from `ga` ends at `en`'s caption, whose own
    # "../summary.md" resolves against `en`'s content dir, so both
    # locales get `en`'s summary text here.  Guard first that the two
    # locales' summaries do differ, since otherwise this would hold
    # however the links had been resolved.
    assert summary("ga") != summary("en")
    for locale in ("en", "ga"):
        assert (assets_dir(locale) / "caption.md").read_text() == summary("en")


def test_served_last_updated_ignores_recommended_flip(
    history: History, built: BuiltRepo, dist: Path
) -> None:
    """End to end, a demo's served ``lastUpdated`` is the time of its
    latest *content* change, not of a later commit that only flips the
    "recommended" flag.

    ``live`` is added and, several commits later, undergoes a change
    which only flips its "recommended" flag.  ``beacon`` is added,
    gets a real content edit, and then has its "recommended" flag
    flipped.  In both cases the flip is the most recent commit
    touching the demo (so is the *defining* commit), but it must not
    advance the mtime.
    """
    import time

    def author_iso(commit_id: str) -> str:
        commit = built.repo[built.commit_oids[commit_id]]
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(commit.author.time))

    def served_last_updated(alias: str) -> str:
        meta = json.loads(
            (dist / history.uuid(alias) / "en" / "metadata.json").read_text()
        )
        return meta["lastUpdated"]

    # Guard: each flip commit carries a different (later) timestamp than the
    # content change whose time should survive.  Without this the two
    # served-lastUpdated checks further down would pass even if lastUpdated
    # tracked the flip, so ensure that the two times really do differ.
    assert author_iso("toggle-live") != author_iso("add-live")
    assert author_iso("recommend-beacon") != author_iso("edit-beacon")

    # live: mtime stays at the add, ignoring the later recommended flip.
    assert served_last_updated("live") == author_iso("add-live")

    # beacon: mtime is the content edit, skipping the later recommended flip.
    assert served_last_updated("beacon") == author_iso("edit-beacon")


def _chapter_count(markdown: str) -> int:
    """Number of top-level (`#`) headings -- i.e. chapters -- in a description."""
    # TODO: Will need updating if we ever, say, put lines starting
    # with '#' inside fenced code blocks in a description file.
    return sum(1 for line in markdown.splitlines() if line.startswith("# "))


def test_descriptions_are_chaptered(dist: Path, dist_demos_index: list[dict]) -> None:
    """Every live demo's description.md is split into `#`-headed chapters,
    with some demos having exactly one and others strictly more."""
    counts = []
    for entry in dist_demos_index:
        description = (
            dist / entry["uuid"] / "en" / "content" / "description.md"
        ).read_text()
        n = _chapter_count(description)
        assert n >= 1, f"{entry['uuid']}: description has no chapters"
        counts.append(n)

    assert min(counts) == 1, "no demo has exactly one chapter"
    assert max(counts) > 1, "no demo has more than one chapter"


def test_some_demos_have_video_thumbnail(
    dist: Path, dist_demos_index: list[dict]
) -> None:
    """Some live demos carry a video thumbnail (extension in the served entry
    and the file present in the dist) and some do not."""
    with_video = [
        e for e in dist_demos_index if e["thumbnailVideoExtension"] is not None
    ]
    without_video = [
        e for e in dist_demos_index if e["thumbnailVideoExtension"] is None
    ]

    assert with_video, "no demo has a video thumbnail"
    assert without_video, "every demo has a video thumbnail"

    for entry in with_video:
        ext = entry["thumbnailVideoExtension"]
        video_path = dist / entry["uuid"] / "en" / "content" / f"thumbnail{ext}"
        assert video_path.is_file(), f"missing video thumbnail for {entry['uuid']}"


def test_bulk_demos_cover_each_kind(
    history: History, dist: Path, dist_demos_index: list[dict]
) -> None:
    """The bulk section yields `count` live demos for every
    (programKind, demoKind) combination, all reaching the served index."""
    from collections import Counter

    bulk_uuids = {d["uuid"] for d in bulk_demos(history.bulk)}
    counts = Counter(
        (e["programKind"], e["demoKind"])
        for e in dist_demos_index
        if e["uuid"] in bulk_uuids
    )

    bulk = history.bulk
    expected = {
        (pk, dk): bulk["count"]
        for pk in bulk["programKinds"]
        for dk in bulk["demoKinds"]
    }
    assert dict(counts) == expected


def test_bulk_demos_names_and_recommended(
    history: History, dist: Path, dist_demos_index
) -> None:
    """Bulk demos have distinct display names, and exactly the first two of
    each category are recommended."""
    from collections import Counter

    by_uuid = {e["uuid"]: e for e in dist_demos_index}
    expected = bulk_demos(history.bulk)

    # Every generated demo's displayName / recommended reach the index as
    # specified, and the names are all distinct.
    names = []
    for demo in expected:
        entry = by_uuid[demo["uuid"]]
        assert entry["displayName"] == demo["display_name"]
        assert entry["recommended"] == demo["recommended"]
        names.append(entry["displayName"])
    assert len(set(names)) == len(names)

    # Exactly two recommended per (programKind, demoKind) category.
    recommended = Counter(
        (d["program_kind"], d["demo_kind"]) for d in expected if d["recommended"]
    )
    assert set(recommended.values()) == {2}
    assert len(recommended) == (
        len(history.bulk["programKinds"]) * len(history.bulk["demoKinds"])
    )


def test_dist_conforms_to_openapi_spec(dist: Path) -> None:
    """The built catalogue validates against the OpenAPI spec.

    ``SPEC_PATH`` is a symlink in the package's data/ dir pointing at the
    spec the front end is served against; if it is absent (or its target
    is missing) the check is skipped rather than failed, so a checkout
    without the spec available does not break the suite.
    """
    if not SPEC_PATH.exists():
        pytest.skip(
            f"OpenAPI spec {SPEC_PATH} is absent; symlink it to "
            "disco-demos-openapi.yaml to run this check"
        )
    report = validate(SPEC_PATH, dist)
    assert report.errors == [], "catalogue does not conform to the spec:\n" + "\n".join(
        report.errors
    )
