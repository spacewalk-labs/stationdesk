#!/usr/bin/env bash
# Make a history-free Git seed containing only the public product tree.
set -euo pipefail
source_root=${1:?product directory required}
output=${2:?output source.bundle required}
source_root=$(cd "$source_root" && pwd)
mkdir -p "$(dirname "$output")"
output=$(cd "$(dirname "$output")" && pwd)/$(basename "$output")
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp -R "$source_root/." "$staging/"
# A built installer is an output, never another nested payload.
[[ ! -e "$staging/.git" ]] || { echo 'The product directory must not contain .git.' >&2; exit 1; }
git -C "$staging" init --quiet --initial-branch=main
git -C "$staging" add --all
# A generated payload has one snapshot commit, not the caller's Git workflow.
# commit-tree avoids running the developer's local post-commit automation here.
tree=$(git -C "$staging" write-tree)
commit=$(printf '%s\n' 'StationDesk installer snapshot' | git -C "$staging" -c user.name=StationDesk -c user.email=installer@stationdesk.invalid commit-tree "$tree")
git -C "$staging" update-ref refs/heads/main "$commit"
git -C "$staging" bundle create "$output" main HEAD
