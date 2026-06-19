#!/usr/bin/env python3
"""Validate a discoverable-demos content directory against a spec

Checks that a *built* demo catalogue directory conforms to a given
OpenAI spec file.  Every language found on disk (a subdirectory of
index/) is validated.  For each language:

  * index/<lang>/demos.json should be a JSON array; its entries should
    validate against the DemoCatalogueEntry schema;

  * for each *live* version (a uuid listed in that language's index):
    every per-demo resource exists, metadata.json validates and its
    uuid matches, and latestUuid equals uuid (a live version points to
    itself);

  * for each *superseded* version (a uuid directory that holds content
    for the language but is NOT in that language's index): only the
    "all"-versions subset of resources is required, its metadata.json
    validates, and its uuid matches the directory name.

Single sources of truth:

  * the list of expected resources is derived from the spec's `paths`;

  * which resources apply to which versions comes from the per-path
    `x-demo-versions` extension ("all" or "live").

The thumbnail path is handled specially in the code, because its
filename (`thumbnail<ext>`) depends on the entry's extension fields
and so cannot be derived from the path template.  It is implicitly
"live" (catalogue cards only).

The DemoCatalogueEntry schema is extracted and used directly.  It is
required that the spec is self-contained (no $ref); this is validated
by this script.  This script also insists on an active date-time
format checker (rfc3339-validator) to ensure the spec's `format:
date-time` is enforced.

A structural problem with the spec or the command-line inputs (missing
`paths`, a malformed path item, a non-existent content directory, ...)
raises SystemExit.

Defects in the *content being validated* (missing resources, bad JSON,
schema violations, broken version pointers) are collected and reported
together with a non-zero exit; finding those is the script's job.

Anything completely unexpected propagates as an uncaught traceback.

Usage example:

    python validate_catalogue.py \
        --spec path/to/disco-demos-openapi.yaml \
        --content-dir path/to/dist-directory
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import click
import yaml
from jsonschema import Draft202012Validator, FormatChecker

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
VERSION_SCOPES = ("all", "live")


def content_path(root: Path, rel: str) -> Path:
    """Resolve a forward-slash relative feed path under the content root."""
    return root.joinpath(*rel.split("/"))


def load_spec(spec_path: Path) -> dict:
    spec = yaml.safe_load(spec_path.read_text())
    if not isinstance(spec, dict):
        raise SystemExit(f"{spec_path}: top-level YAML is not a mapping")
    return spec


def spec_paths(spec: dict, spec_path: Path) -> dict:
    """Return the spec's `paths` mapping; fail if absent."""
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise SystemExit(f"{spec_path}: spec has no 'paths' mapping")
    return paths


# --- Expected resources, derived from the spec's paths -----------------

def catalogue_index_template(paths: dict) -> str:
    """The catalogue index path template — the lone path without {uuid}.  Keeps
    the {language} placeholder so it can be substituted per discovered language."""
    candidates = [t.lstrip("/") for t in paths if "{uuid}" not in t]
    if len(candidates) != 1:
        raise SystemExit(
            "expected exactly one catalogue-level path (without {uuid}) in the "
            f"spec, found: {candidates!r}"
        )
    template = candidates[0]
    if "{language}" not in template:
        raise SystemExit(
            f"catalogue index path {template!r} has no {{language}} placeholder"
        )
    return template


def per_demo_templates_by_scope(paths: dict, spec_path: Path) -> dict[str, list[str]]:
    """Map version-scope -> relative path templates, for the fixed per-demo
    files (paths whose only placeholders are {uuid}/{language}).  Scope comes
    from each path item's `x-demo-versions` extension.  Paths with other
    placeholders (the thumbnail) are handled in code, not here."""
    by_scope: dict[str, list[str]] = {scope: [] for scope in VERSION_SCOPES}
    for template, item in paths.items():
        placeholders = set(PLACEHOLDER_RE.findall(template))
        # Keep fixed per-demo paths: must contain {uuid}, with no placeholders
        # beyond {uuid}/{language}.
        if not {"uuid"} <= placeholders <= {"uuid", "language"}:
            continue
        if not isinstance(item, dict):
            raise SystemExit(
                f"{spec_path}: path item for {template!r} is not a mapping"
            )
        scope = item.get("x-demo-versions")
        if scope not in VERSION_SCOPES:
            raise SystemExit(
                f"{spec_path}: per-demo path {template!r} must declare "
                f'x-demo-versions as one of {VERSION_SCOPES}, got {scope!r}'
            )
        by_scope[scope].append(template.lstrip("/"))
    if not any(by_scope.values()):
        raise SystemExit(
            f"{spec_path}: found no per-demo resource paths (with {{uuid}} and "
            "only {uuid}/{language} placeholders); has the spec changed?"
        )
    return by_scope


# --- Validator built directly from the DemoCatalogueEntry component ----

def schema_refs(node: Any, path: str = "#") -> Iterator[str]:
    """Yield the location of every "$ref" found within a schema node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield path
            else:
                yield from schema_refs(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from schema_refs(item, f"{path}/{index}")


def build_entry_validator(spec: dict, spec_path: Path) -> Draft202012Validator:
    try:
        entry_schema = spec["components"]["schemas"]["DemoCatalogueEntry"]
    except (KeyError, TypeError) as exc:
        raise SystemExit(
            f"{spec_path}: could not find components.schemas.DemoCatalogueEntry "
            f"({exc!r})"
        )

    # This script validates entries against the extracted schema with no ref
    # resolution, so the schema must be self-contained.
    refs = list(schema_refs(entry_schema))
    if refs:
        raise SystemExit(
            f"{spec_path}: DemoCatalogueEntry now contains $ref(s) at: "
            f"{', '.join(refs)}.\n"
            "This validator assumes the entry schema is self-contained and "
            "validates it without resolving references.  Restore ref-aware "
            "validation (e.g. a `referencing` registry built from the whole "
            "spec) before relying on this script."
        )

    # The spec annotates lastUpdated with `format: date-time`.  jsonschema only
    # enforces that format if rfc3339-validator (or strict-rfc3339) is
    # installed; otherwise the check silently passes everything.  conforms()
    # returns True for a format with no active checker, so this probe with a
    # deliberately invalid value tells us whether date-time is really enforced.
    format_checker = FormatChecker()
    if format_checker.conforms("not-a-date-time", "date-time"):
        raise SystemExit(
            "date-time format checking is inactive: install rfc3339-validator "
            "(pip install rfc3339-validator) so the spec's `format: date-time` "
            "is enforced rather than silently skipped."
        )
    return Draft202012Validator(entry_schema, format_checker=format_checker)


# --- Validation ---------------------------------------------------------

@dataclass
class Report:
    errors: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def schema_errors(self, where: str, validator, instance) -> bool:
        """Record any schema errors for `instance`; return True if there were any."""
        errs = sorted(
            validator.iter_errors(instance),
            key=lambda e: [str(p) for p in e.absolute_path],
        )
        for err in errs:
            loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
            self.fail(f"{where}: at {loc}: {err.message}")
        return bool(errs)


def discover_languages(root: Path, index_template: str, report: Report) -> list[str]:
    """Languages present on disk: the subdirectories of the index template's
    parent (e.g. `index/`).  Reported as a finding (not crashed) if absent."""
    index_parent = index_template.split("{language}", 1)[0].rstrip("/")
    base = content_path(root, index_parent)
    if not base.is_dir():
        report.fail(f"no index directory {index_parent!r}/ under the content root")
        return []
    languages = sorted(
        p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    if not languages:
        report.fail(f"{index_parent}/: no language subdirectories found")
    return languages


def superseded_version_dirs(
    root: Path, language: str, live_uuids: set[str]
) -> list[str]:
    """Top-level uuid directories that hold content for `language` but are not
    listed in that language's index — superseded versions for it.  A demo absent
    in a language has no <uuid>/<language>/ directory and is simply not one of
    that language's versions."""
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and p.name != "index"
        and not p.name.startswith(".")
        and p.name not in live_uuids
        and (p / language).is_dir()
    )


def check_metadata(
    path: Path,
    rel: str,
    uuid: str,
    entry_validator: Draft202012Validator,
    report: Report,
    require_latest_is_self: bool,
    live_uuids: set[str],
) -> None:
    try:
        meta = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        report.fail(f"{rel}: could not read/parse: {exc}")
        return
    report.schema_errors(rel, entry_validator, meta)
    if isinstance(meta, dict):
        if meta.get("uuid") != uuid:
            report.fail(
                f"{rel}: uuid {meta.get('uuid')!r} != directory uuid {uuid!r}"
            )
        latest = meta.get("latestUuid")
        if require_latest_is_self:
            if latest != uuid:
                report.fail(
                    f"{rel}: latestUuid {latest!r} != uuid {uuid!r} "
                    "(a live version must point to itself)"
                )
        elif latest is not None and latest not in live_uuids:
            report.fail(
                f"{rel}: latestUuid {latest!r} is neither null (a deleted "
                "version) nor a uuid listed in the catalogue index (a "
                "superseded version must point to the live version)"
            )


def check_fixed_resources(
    root: Path,
    templates: list[str],
    uuid: str,
    language: str,
    entry_validator: Draft202012Validator,
    report: Report,
    require_latest_is_self: bool,
    live_uuids: set[str],
) -> None:
    for template in templates:
        rel = template.replace("{uuid}", uuid).replace("{language}", language)
        path = content_path(root, rel)
        if not path.is_file():
            report.fail(f"{uuid}: missing resource {rel!r}")
        elif path.name == "metadata.json":
            check_metadata(
                path,
                rel,
                uuid,
                entry_validator,
                report,
                require_latest_is_self,
                live_uuids,
            )


def validate(spec_path: Path, root: Path) -> Report:
    spec = load_spec(spec_path)
    paths = spec_paths(spec, spec_path)
    entry_validator = build_entry_validator(spec, spec_path)
    index_template = catalogue_index_template(paths)
    by_scope = per_demo_templates_by_scope(paths, spec_path)
    report = Report()

    for language in discover_languages(root, index_template, report):
        validate_language(
            root, language, index_template, by_scope, entry_validator, report
        )

    return report


def validate_language(
    root: Path,
    language: str,
    index_template: str,
    by_scope: dict[str, list[str]],
    entry_validator: Draft202012Validator,
    report: Report,
) -> None:
    index_rel = index_template.replace("{language}", language)
    all_version_templates = by_scope["all"]
    live_only_templates = by_scope["live"]

    try:
        catalogue = json.loads(content_path(root, index_rel).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        report.fail(f"could not read catalogue {index_rel!r}: {exc}")
        return

    if not isinstance(catalogue, list):
        report.fail(f"{index_rel}: expected a JSON array")
        return

    # --- Live versions: every uuid listed in this language's index --------
    live_uuids: set[str] = set()
    for i, entry in enumerate(catalogue):
        # Record the uuid (if present) before validation so a malformed live
        # entry's directory is never misclassified as a superseded version.
        if isinstance(entry, dict) and isinstance(entry.get("uuid"), str):
            live_uuids.add(entry["uuid"])

        # Only derive resource paths from entries we know are well-formed; a
        # schema-valid entry is guaranteed to have every required field with
        # the right type, so the field accesses below need no guards.
        if report.schema_errors(f"{index_rel} (entry {i})", entry_validator, entry):
            continue
        uuid = entry["uuid"]
        prefix = f"{uuid}/{language}"

        if entry["latestUuid"] != uuid:
            report.fail(
                f"{index_rel} (entry {i}): latestUuid {entry['latestUuid']!r} "
                f"!= uuid {uuid!r} (a live version must point to itself)"
            )

        check_fixed_resources(
            root,
            all_version_templates + live_only_templates,
            uuid,
            language,
            entry_validator,
            report,
            require_latest_is_self=True,
            live_uuids=live_uuids,
        )

        # Thumbnails: filename depends on the entry's extension fields, so it
        # cannot be derived from the spec's {thumbnailFilename} path template.
        # Extensions include the leading dot, matching the webapp's URL builders.
        thumbnails = [f"{prefix}/content/thumbnail{entry['thumbnailImageExtension']}"]
        if entry["thumbnailVideoExtension"] is not None:
            thumbnails.append(
                f"{prefix}/content/thumbnail{entry['thumbnailVideoExtension']}"
            )
        for rel in thumbnails:
            if not content_path(root, rel).is_file():
                report.fail(f"{uuid}: missing resource {rel!r}")

    # --- Superseded versions for this language ----------------------------
    for uuid in superseded_version_dirs(root, language, live_uuids):
        check_fixed_resources(
            root,
            all_version_templates,
            uuid,
            language,
            entry_validator,
            report,
            require_latest_is_self=False,
            live_uuids=live_uuids,
        )


def main(spec: Path, content_dir: Path) -> None:
    """Validate a generated demo content directory against the OpenAPI spec.

    Every language found under index/ is validated.
    """
    report = validate(spec, content_dir)

    if report.errors:
        click.echo(f"FAILED — {len(report.errors)} problem(s):", err=True)
        for err in report.errors:
            click.echo(f"  - {err}", err=True)
        raise SystemExit(1)

    click.echo("OK — catalogue conforms to the OpenAPI contract.")


if __name__ == "__main__":
    main()
