# Maintenance of "latest version" of old demos

## Background

The revision history of a set of "demos" is stored in git.  They fit
within a wider programming environment.  Users can create a project
"linked to" a particular demo, which means the user can work with a
copy of that demo, and can also see explanatory content associated
with that demo.  An important feature of this scheme is that the
original demo project (which is fetched from a server) is copied for
the user at the moment the user creates their project, but the
explanatory content is fetched afresh each time the user works with
that project in the future.

The collection of demos will change over time in a few ways:

* The set of available demos will generally expand, although we will
  sometimes remove demos from the collection.

* Each individual demo will undergo minor changes, for example to fix
  typos in the explanatory content or update graphical assets used in
  the project.

* Sometimes a particular demo will undergo a "major version" change,
  meaning that we want the previous version to longer be discoverable,
  although for any extant projects linked to the previous version, the
  explanatory content must remain available.

At a particular commit, information for each current demo is
represented by a subdirectory (at arbitrary depth) within the repo.
As errors are discovered, or improvements arise, to a particular demo,
new commits are made which affect the files within its subdirectory.
So far this is a straightforward use of git as a version control
system.  However, there are some other properties which are less
standard.  Some of these are outlined above; all are explained in more
detail below.

### Major version identified by UUID stored in file

The identity of a particular "major version" of a demo consists in a
UUID stored in a file `pytch-demo-uuid.txt`.  The (sub)directory
containing that file is the "root" of the demo so identified.

When a change to the demo content (either its project or the
explanatory content) is meaningful enough to count as a new major
version, a fresh UUID is generated and written to that file.  This
will therefore show up as a change to that file in the git commit.

### Ignored data

A demo has various pieces of metadata associated with it.  One of
these specifies whether a demo is "recommended" or not, stored as a
Boolean value within a JSON file.  If a demo changes from
"recommended" to "not recommended", or vice versa, this does not count
as a modification of that demo.  See below for details of this file
and the JSON structure within it.

### Moving a demo's subdirectory

We sometimes reorganise the repo, for example by introducing another
layer of hierarchy, or rearranging demos within the existing
hierarchy.  This does not count as a modification of the demo.

It is not possible to distinguish the situation where we intend to
both move a demo's subdirectory _and_ update it to a new major version
(see next) from the situation where we have deleted one demo and
created an entirely unrelated one.  Only moves which preserve the
demo's UUID are treated as moves.

### Releasing a new "major version" of a demo

We sometimes realise there is an altogether better way to illustrate
the concept within a particular demo.  In that case, we create a new
"major version" of that demo.  Behaviour of the app in this situation
is as follows:

When a user works with an existing project which was created with
reference to an earlier major version of the demo, the app should:

* continue to show the newest explanatory content for that earlier
  major version.

* show a notice saying that a newer major version of this demo is
  available, with a link to the newest such major version.

### Updates to old major versions

Even after a new major version has been released, we sometimes make
changes to an old major version's explanatory content, for example to
fix a typo.


## Representation of data in git history and file tree

### Ignored data

The "recommended" flag mentioned above lives in a file `metadata.json`
stored within a directory with name of the form `by-locale/en/`, where
the `en` path component stands for any two-letter language code.  The
flag is the Boolean value of the property `recommended` of the
top-level object represented by the JSON contents of the
`metadata.json` file.  Changes to the metadata other than to the
`recommended` flag are _not_ to be ignored.

### Searching for all demo-major-versions

The set of demo-major-versions which have ever existed in the repo
history can be found by searching every commit for files with the name
`pytch-demo-uuid.txt` and collecting their contents into a set of
UUIDs.

### Releasing a new demo-major-version

A new major version of a demo is created by making a commit which
replaces the contents of the `pytch-demo-uuid.txt` file with a new
UUID, and also updates the actual content of the demo.  From this
point on, the previous major version does not exist in the tree of the
HEAD commit.  Its content is available only in earlier commits.

### Updating a old demo-major-version

This is done by creating a branch off the git history at the point
where the most recent change was made to the demo-major-version,
fixing the typo in the branch, and merging the branch back in to the
main line of the history, but not keeping any content from that
branch's tree.

### Behaviour in the presence of non-linear history

In the case of branches and merges of history, it would be possible in
rather particular circumstances for there to be ambiguity about the
correct state of a particular demo.  For example, suppose part of
the commit graph is

``` text
A -> B -> C -> D ----+-> G -> H = HEAD
      \             /
       +-> E -> F -+
```

and `C` and `E` make (different) changes to a particular demo (`d1`),
and `D` and `F` both upgrade the major version of that same demo to
identical new content, creating `d2`.  Then what is "the most recent
state" of demo `d1` as of the `HEAD`?  It is not well defined.  The
data extraction process, described below, checks for this situation
and raises an error.  The error can be corrected by adding two more
commits from `H`, a child which introduces the definitive content for
demo `d1` (i.e., adds the entire contents of its subdirectory at an
arbitrary place in the git tree), and a grandchild which removes it
again.

With the same commit graph structure, consider a different situation.
Suppose `A` defines a new demo-major-version `d1`, `F` updates `d1`,
and this update is preserved in `G` and `H`.  Commits `B`, `C`, `D`,
`E`, `G`, and `H` do not change `d1`.  In this case, "the most recent
state" of `d1` is unambiguously that contained in `F`.


## Data to be extracted from git repo history

The build tool extracts two sets of data from the git history.  The
output is JSON written to stdout, with the precise structure given in
each section below.  The tool is written in Python using the `pygit2`
library for interacting with the git repo, and other libraries as
appropriate for any well-known algorithms required as part of the
processing.

### Chain linking major versions of each demo

A commit which replaces the contents of a `pytch-demo-uuid.txt` file
creates a link from the demo-major-version identified by the old
contents of that file to the demo-major-version identified by the new
contents of that file.

The tool creates a data structure mapping each demo-major-version UUID
`demoUuid` to the UUID of the most recent demo-major-version of the
chain containing `demoUuid`.  For the most recent demo-major-version,
this will be a link from a UUID to itself.

The output value is an array of two-element arrays whose first element
is the (potentially) old demo-major-version UUID (call it `d1`), and
whose second element is the most recent demo-major-version UUID of the
chain to which `d1` belongs.  This output array is the value of the
property `majorVersionChainHeadRecords` in the overall output JSON
object.

### Git _commit_ of most recent version of each demo-major-version

For every demo-major-version (identified by UUID, recall), the tool
finds the git commit which created "the most recent state" of that
demo-major-version.

Situations where "the most recent" state is not well-defined are
detected and an error raised, halting processing of the repo.

The output is an array of three-element arrays.  The first element is
the demo-major-version UUID; the second element is the git SHA1 of the
commit which created the most recent version of that
demo-major-version; the third element is the path, within that
commit's tree, at which the demo-major-version's `pytch-demo-uuid.txt`
file can be found.  This output array is the value of the property
`majorVersionDefiningCommit` in the overall output JSON object.
