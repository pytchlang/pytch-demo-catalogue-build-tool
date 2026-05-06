# Build-tool for discoverable demos for Pytch


## In-repo structure of each demo

The "repo" referred to in this section is the repository containing
the demos.  This is distinct from the repo containing this README
file, which is the repo containing the tool to process such demos.

Each demo has its own "root" directory (folder) somewhere under
`$DEMO_CATALOGUE_REPO_ROOT/demos/`.  For example, we might have a demo
whose root is:

``` shell
$DEMO_CATALOGUE_REPO_ROOT/demos/animations/friendly/flowers/
```

In the rest of this section, we'll refer to that directory as
`$DEMO_ROOT`.

* `$DEMO_ROOT/pytch-demo-uuid.txt` — Contains one line of text which
  is a random uuid.  Identifies "major version" of this demo.

* `$DEMO_ROOT/metadata.json` — JSON representing an object with the
  following properties:

  * `authorName`
  * `demoKind`

* `$DEMO_ROOT/by-locale/en/` — Directory containing content for
  language `en`.  Within this directory are:

  * `$DEMO_ROOT/by-locale/en/metadata.json` — JSON representing an
    object giving language-specific metadata with the following
    properties:

    * `recommended`

  * `$DEMO_ROOT/by-locale/en/content/` — Directory containing the
    English content for human consumption.  Within this directory are:

    * `$DEMO_ROOT/by-locale/en/content/description.md` — Markdown file
      for display in the "activity content" pane of the front-end app.
      Should be divided into chapters using top-level headings (lines
      starting with a single hash character).

    * `$DEMO_ROOT/by-locale/en/content/summary.md` — Short markdown
      file, shown as summary in the "browse demos" page, and also as a
      summary in the activity content pane.  A sentence or two.

    * `$DEMO_ROOT/by-locale/en/content/thumbnail.png` — 480×360
      screenshot image.  Can also be `thumbnail.jpg` or any other
      image format.  Exactly one image file should be present.

    * `$DEMO_ROOT/by-locale/en/content/thumbnail.mp4` — **Optional**
      480×360 video.  Other video formats are OK too.  At most one
      video file should be present.

    * `$DEMO_ROOT/by-locale/en/content/assets/` — **Optional.  Work in
      progress.**  Directory containing any images, videos, etc.,
      referred to by `description.md`.  If (say) an image is shared
      across multiple languages, symlinks can be used to maintain one
      source of truth within the repo.

  * `$DEMO_ROOT/by-locale/en/project/` — Directory containing the
    extracted contents of a Pytch project zipfile.

* `$DEMO_ROOT/by-locale/ga/` — Directory containing content for
  language `ga`.  Same structure as for language `en` just described.

* `$DEMO_ROOT/by-locale/de/` — Directory containing content for
  language `de`.  Etc.


## Structure of served content

The front-end requires the information in a different structure, so
this repo also contains a tool to convert the repo to a "distribution"
structure.  This is written to `$REPO_ROOT/dist/`, which is therefore
git-ignored.

The structure, relative to a "demo catalogue base" URL, is as follows.

* `index/en/demos.json` — Catalogue of demos available in language
  `en`, as JSON representing an array.  See below for details of its
  structure.

* `index/ga/demos.json` — Catalogue of demos available in language
  `ga`, similarly.

* `index/de/demos.json` — Catalogue of demos available in language
  `de`.  Etc.

* `e9fba26e-4a05-4cba-bb7f-e3e12446aaf6/en/` — Directory containing
  content in language `en` for the demo with the given uuid.  There
  will also be similarly-named sibling directories for other demos
  and/or other languages.  This directory contains the following:

  * `e9⋯f6/en/metadata.json` — Combination of locale-independent and
    locale-specific metadata.  Matches type `DemoCatalogueEntry` in
    front-end webapp.

  * `e9⋯f6/en/content/` — Directory containing content (in language
    `en`) directly copied from repo:

    * `e9⋯f6/en/content/description.md`

    * `e9⋯f6/en/content/summary.md`

    * `e9⋯f6/en/content/assets/` — Directory containing assets needed
      for the description content, e.g., screenshots, diagrams.

    * `e9⋯f6/en/content/thumbnail.png` — _(Only present under the uuid
      for the current version of the demo.)_  Screenshot image.  Can
      also be `.jpg` or other image format.

    * `e9⋯f6/en/content/thumbnail.mp4` — _(Optional.  Only present
      under the uuid for the current version of the demo.)_
      Screenshot video.  Can also be other video format.

  * `e9⋯f6/en/project.zip` — _(Only present under the uuid for the
    current version of the demo.)_  Pytch zipfile for the demo.

### Structure of `index/en/demos.json` file

The JSON should represent an array, each element of which is an object
with the following properties, shown using TypeScript notation for
types:

* `uuid: string` — Unique identifier for current major version of the
  demo.  (Will be duplicated on server for all languages, but doesn't
  matter because only one source of truth in repo.)  **Source:**
  Contents of `pytch-demo-uuid.txt` file in repo.

* [[ Perhaps in the future: `path: string` or `Array<string>` for
  components; or alternatively `tags: Array<string>` ]]

* `displayName: string` — Also used as project name when creating new
  project linked to thid demo.  **Source:** Extracted from metadata in
  project as-per-zipfile content.

* `authorName: string` — Who wrote this demo.  **Source:** Property in
  demo's global `metadata.json` file.

* `programKind: PytchProgramKind` — Which kind of Pytch program
  (`"per-method"` or `"flat"`).  **Source:** Common value of the
  `"kind"` value in each locale's project as-per-zipfile content; if
  there is disagreement, an error is raised.

* `demoKind: DemoKind` — Whether intended as a full demo, or just a
  snippet to be copied by the user into their own project.  (Strings
  are `"snippet"` and `"game"`, although these enum strings are
  presented differently to the user by the front end.)  **Source:**
  Property in demo's global `metadata.json` file.

* `summaryMarkdown: string` — **Source:** Contents of `summary.md`
  file in repo.

* `lastUpdated: string` — ISO8601 (long form, i.e., with “-”s and
  “:”s) to whole-second precision, in UTC, marked with “Z” suffix.
  (Can vary by locale.)  **Source:** Computed by build tool from git
  commit history.

* `recommended: boolean` — Whether this demo should appear in the
  carousel of recommended demos.  **Source:** Property of
  locale-specific `metadata.json` file.

* `thumbnailImageExtension: string` — What extension the thumbnail
  image has.  Expect will be `".jpg"` or `".png"` but others are
  possible.  **Source:** Computed by build tool by looking at what
  image file exists in the repo.

* `thumbnailVideoExtension: string | null` — Optionally, what
  extension the thumbnail video has.  Expect `".mp4"` or maybe others.
  Not all demos have thumbnail videos so, as noted in type, this can
  be `null`.  **Source:** Computed by build tool by looking at what
  video file exists, if any, in the repo.

* `latestUuid: string` — **Work in progress.** The uuid of the latest
  release of this demo.  Not yet implemented.  Idea is that if we
  significantly update a demo, it would be useful to show a message
  along the lines of "There is a newer version of this demo available.
  Click here to create a project linked to it.".  **Source:** Will be
  computed from git history.

This type is represented in the Python tool here (see below) as the
type `CatalogueEntry`.  In the front-end, the zod object is
`zDemoCatalogueEntry` and the type is `DemoCatalogueEntry`.


## Tools

The `$REPO_ROOT/build-tool` directory contains the tool which turns
the demo content as stored in this repo into the "distribution" ready
to be served.  The tools must be run under `poetry`, as shown below.

### Tool to build distribution structure

``` shell
cd $REPO_ROOT
poetry run -P build-tool build-dist $DEMO_CATALOGUE_REPO_ROOT dist
```

Reads all demos under `$DEMO_CATALOGUE_REPO_ROOT` and writes a
distribution file structure under `dist/`.  In principle, a directory
other than `dist` could be specified, but this is unlikely to be
useful.

### Tool to create a new demo

A common use case is that a demo author has created, in Pytch, a
project suitable to be a demo.  They can then run, for example,

``` shell
cd $REPO_ROOT
poetry run -P build-tool new-demo \
    $DEMO_CATALOGUE_REPO_ROOT/demos/whizzy-games/feed-the-kittens \
    en \
    feed-kittens.zip
```

(shown on multiple lines here but can be typed on one).

The parts of this command which will vary case by case are:

* `$DEMO_CATALOGUE_REPO_ROOT/demos/whizzy-games/feed-the-kittens` —
  Directory which will hold the content for the new demo.

* `en` — What language this demo will be in.

* `feed-kittens.zip` — The zipfile, downloaded from Pytch, containing
  the project itself.

It is permissible for the demo to already exist but not in the given
language, in which case a new language is added to the existing demo.

The result will be a collection of files including some marked with
TODOs.  Once the content of the `description.md` and `summary.md`
files has been written, and the metadata filled in, the new files can
all be committed to git.
