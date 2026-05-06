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
