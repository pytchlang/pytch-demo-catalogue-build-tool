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
        assert (
            record.latest_uuid == want_latest
        ), f"{alias}: latest_uuid {record.latest_uuid} != {want_latest}"
        assert record.present_at_head == want["present"], alias

        if "defined_at" in want:
            want_sha = built.commit_oids[want["defined_at"]]
            got_sha = extractor.defining_commits[uuid].sha1
            assert got_sha == want_sha, (
                f"{alias}: defined_at {got_sha} != {want_sha} "
                f"({want['defined_at']})"
            )

        if "program_kind" in want:
            assert record.common_program_kind() == want["program_kind"], alias


@pytest.mark.parametrize(
    "scenario", _scenarios(with_error=True), ids=lambda s: s["start_ref"]
)
def test_ambiguous_history_raises(built: BuiltRepo, scenario: dict) -> None:
    with pytest.raises(RuntimeError, match="[Aa]mbiguous"):
        Extractor(built.repo, scenario["start_ref"])


def test_build_dist_marks_deleted_demo(history: History, dist: Path) -> None:
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
    assert "Reword the story demo's description (a content change)" in description(
        "descr"
    )

    # The index lists exactly the live demos: the named live ones plus every
    # generated bulk demo, and none of the deleted/superseded versions.
    index = json.loads((dist / "index" / "en" / "demos.json").read_text())
    listed = {e["uuid"] for e in index}
    named_live = {history.uuid(a) for a in ("live", "superB", "mover", "descr")}
    bulk_uuids = {d["uuid"] for d in bulk_demos(history.bulk)}
    assert listed == named_live | bulk_uuids
    for a in ("gone", "chainA", "chainB", "superA"):
        assert history.uuid(a) not in listed


def _chapter_count(markdown: str) -> int:
    """Number of top-level (`#`) headings -- i.e. chapters -- in a description."""
    return sum(1 for line in markdown.splitlines() if line.startswith("# "))


def test_descriptions_are_chaptered(dist: Path) -> None:
    """Every live demo's description.md is split into `#`-headed chapters,
    with some demos having exactly one and others strictly more."""
    index = json.loads((dist / "index" / "en" / "demos.json").read_text())

    counts = []
    for entry in index:
        description = (
            dist / entry["uuid"] / "en" / "content" / "description.md"
        ).read_text()
        n = _chapter_count(description)
        assert n >= 1, f"{entry['uuid']}: description has no chapters"
        counts.append(n)

    assert min(counts) == 1, "no demo has exactly one chapter"
    assert max(counts) > 1, "no demo has more than one chapter"


def test_some_demos_have_video_thumbnail(dist: Path) -> None:
    """Some live demos carry a video thumbnail (extension in the served entry
    and the file present in the dist) and some do not."""
    index = json.loads((dist / "index" / "en" / "demos.json").read_text())

    with_video = [e for e in index if e["thumbnailVideoExtension"] is not None]
    without_video = [e for e in index if e["thumbnailVideoExtension"] is None]

    assert with_video, "no demo has a video thumbnail"
    assert without_video, "every demo has a video thumbnail"

    for entry in with_video:
        ext = entry["thumbnailVideoExtension"]
        video_path = dist / entry["uuid"] / "en" / "content" / f"thumbnail{ext}"
        assert video_path.is_file(), f"missing video thumbnail for {entry['uuid']}"


def test_bulk_demos_cover_each_kind(history: History, dist: Path) -> None:
    """The bulk section yields `count` live demos for every
    (programKind, demoKind) combination, all reaching the served index."""
    from collections import Counter

    index = json.loads((dist / "index" / "en" / "demos.json").read_text())
    bulk_uuids = {d["uuid"] for d in bulk_demos(history.bulk)}
    counts = Counter(
        (e["programKind"], e["demoKind"])
        for e in index
        if e["uuid"] in bulk_uuids
    )

    bulk = history.bulk
    expected = {
        (pk, dk): bulk["count"]
        for pk in bulk["programKinds"]
        for dk in bulk["demoKinds"]
    }
    assert dict(counts) == expected


def test_bulk_demos_names_and_recommended(history: History, dist: Path) -> None:
    """Bulk demos have distinct display names, and exactly the first two of
    each category are recommended."""
    from collections import Counter

    index = json.loads((dist / "index" / "en" / "demos.json").read_text())
    by_uuid = {e["uuid"]: e for e in index}
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
