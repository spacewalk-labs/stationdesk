#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source_checkout="$(realpath "$1")"
output="$(realpath -m "$2")"
cd "$source_checkout"
git apply "$here/guard-source.patch"
npm ci --ignore-scripts --include-workspace-root \
  --workspace=@getpaseo/protocol --workspace=@getpaseo/client \
  --workspace=@getpaseo/server --workspace=@getpaseo/highlight \
  --workspace=@getpaseo/plugin --workspace=@getpaseo/relay --workspace=@getpaseo/cli
npm run build --workspace=@getpaseo/protocol
npm run build --workspace=@getpaseo/client
npm run build --workspace=@getpaseo/highlight --workspace=@getpaseo/plugin --workspace=@getpaseo/relay
npm run build --workspace=@getpaseo/server
node "$here/verify-guards.mjs" "$source_checkout"
python3 "$here/rebuild-bundles.py" "$source_checkout" "$output"
(cd "$output" && sha256sum --check "$here/../SHA256SUMS")
