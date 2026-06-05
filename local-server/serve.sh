#!/bin/bash -e

: "${PYTCH_LOCAL_SERVER_DIR:?}"

if [ ! "$1" ]; then
    1>&2 echo Usage: "$(basename "$0")" DEMO-CONTENT-REPO-ROOT
fi

content_repo_root="$1"

cd "$(realpath "$(dirname "$0")"/..)"

echo Building content from repo "${content_repo_root}"
if ! poetry run -P build-tool \
     build-dist "${content_repo_root}" "${content_repo_root}"/dist;
then
    if [ -n "$TMUX" ]; then
        1>&2 echo
        1>&2 echo Sleeping so you can read this in tmux.
        sleep 600
    fi
    exit 1
fi

cd "${content_repo_root}"/dist

echo Serving demo catalogue from "$(pwd)"
exec python "$PYTCH_LOCAL_SERVER_DIR"/cors_server.py 8130
