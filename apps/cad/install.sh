#!/usr/bin/env bash
# Public assets need no credentials; GH_TOKEN supports private development assets.
set -euo pipefail
runtime="$STATIONDESK_ROOT/runtime/cad"
web="$STATIONDESK_ROOT/web/cad"
mkdir -p "$runtime"
work=$(mktemp -d "$runtime/download.XXXXXX")
trap 'rm -rf "$work"' EXIT
case "$(uname -m)" in
    x86_64) arch=x86_64 ;;
    aarch64) arch=aarch64 ;;
    *) echo "Unsupported CAD architecture: $(uname -m)" >&2; exit 1 ;;
esac
# The existing web-bundle/VERSION pins the extracted web files; VERSION pins
# downloads. Existing installs need no cached archive or credentials to verify.
web_checks() {
    awk -v base="$1" 'length($1) == 64 && $2 ~ /^dist\// {
        sub(/^dist\//, "", $2); print $1 "  " base "/" $2
    }' "$STATIONDESK_APP_DIR/web-bundle/VERSION"
}
for component in web "cli-$arch"; do
    read -r url sha < <(awk -v component="$component" '$1 == component { print $2, $3 }' "$STATIONDESK_APP_DIR/VERSION") || true
    if [[ -z "$url" || -z "$sha" ]]; then
        echo "No CAD asset pinned for $component" >&2
        exit 1
    fi
    if [[ "$component" == web ]]; then
        if web_checks "$web/app" | sha256sum --status -c -; then continue; fi
    elif printf '%s  %s\n' "$sha" "$runtime/cadcraft-cli" | sha256sum --status -c -; then
        continue
    fi
    headers=(-H 'Accept: application/octet-stream')
    if [[ -n "${GH_TOKEN:-}" ]]; then headers+=(-H "Authorization: Bearer $GH_TOKEN"); fi
    if ! curl -fLsS --max-time 180 "${headers[@]}" "$url" -o "$work/$component"; then
        echo "CAD 파일을 내려받지 못했습니다 ($component). 네트워크와 Release 주소를 확인하십시오. 비공개 개발 Release는 읽기 권한이 있는 GH_TOKEN이 필요합니다." >&2
        exit 1
    fi
    printf '%s  %s\n' "$sha" "$work/$component" | sha256sum -c -
done
# Finish all downloads, checks and extraction before touching the installation.
if [[ -f "$work/web" ]]; then
    mkdir "$work/app"
    tar -xzf "$work/web" -C "$work/app"
    web_checks "$work/app" | sha256sum -c -
fi
copy_file() {
    if ! cmp -s "$1" "$2"; then install -m"$3" "$1" "$2"; fi
}
if [[ -f "$work/cli-$arch" ]]; then
    copy_file "$work/cli-$arch" "$runtime/cadcraft-cli" 755
fi
mkdir -p "$web/app/licenses" "$HOME/cad"
if [[ -d "$work/app" ]]; then
    for file in "$work/app/"*; do copy_file "$file" "$web/app/$(basename "$file")" 644; done
fi
for file in "$STATIONDESK_APP_DIR/licenses/"*; do
    copy_file "$file" "$web/app/licenses/$(basename "$file")" 644
done
for file in index.html cad.svg; do copy_file "$STATIONDESK_APP_DIR/$file" "$web/$file" 644; done
