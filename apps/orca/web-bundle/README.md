# Orca web bundle

`dist/` is the **built** Orca web client that StationDesk serves at `/orca-web/` behind
the owner gate, in place of the raw client that `orca serve` ships. `../nginx/serve.conf`
serves it from this folder and 302-redirects the vendor client URL to it.

## Why vendor a build (not the source)

The client is a static, self-contained build (`web-index.html` + hashed `assets/`).
Serving it needs no toolchain, so only the **built dist** is vendored here — not the
upstream source and not the build tooling. The dist is a derivative of the upstream
Orca web client (MIT) plus local patches (also MIT); see `LICENSE` and `licenses/THIRD-PARTY-LICENSES.txt`.

## What the patches change (all baked into `dist/`)

- **PTY-reload fix** — upstream bug: after a full page reload the terminal panes die
  with *"Local PTYs are unavailable in the web client"* forever. The fix maps the
  paired web client's "local" host to the active runtime so terminals survive reload.
- **Runtime-rebind** — a *"Reconnecting…"* overlay + automatic re-attach when the
  runtime restarts, instead of a dead terminal.
- **Local browser render** and **grab** (element-comment) — present in the bundle but
  **inert unless extra sidecars are installed** (a slot manager / a grab bridge, which
  StationDesk does not deploy). With those absent, the client falls back to the
  upstream behavior (streaming render; no grab). See "Optional local-browser services" below.

## Integrity: VERSION + verify-web-bundle.sh

`VERSION` records the matching upstream AppImage version, entry asset, file
count and SHA-256 of the complete tree. `../install.sh` runs
`../bin/verify-web-bundle.sh` against that pin before serving the bundle.
The check detects incomplete or changed files; it does not rebuild the client.
When replacing `dist/`, update every `VERSION` field in the same change.

## Bundled text

Local patch UI strings use English. Upstream i18n locale bundles, including
Korean and native-script language names, and the CJK text-measurement sample
remain part of the client. They are upstream localization and library data.

## Source and licenses

The upstream build input is `stablyai/orca` tag `v1.4.139`, commit
`0ab2e001d16839f5c95cbda84fffb46f08e77359` (MIT). `LICENSE` preserves its
copyright and permission notice; the local web modifications are MIT too.
`licenses/THIRD-PARTY-LICENSES.txt` includes the npm package license and notice
texts resolved by that commit's lockfile for the browser inputs.
`licenses/packages.json` records their exact versions, tarball URLs and integrity.
The inventory covers the renderer/shared browser imports and runtime dependency
closure, plus the redistributed TypeScript worker; tree shaking can omit parts.
The AppImage's native/runtime dependencies are outside this static web bundle.

`licenses/asset-sources.json` maps the bundled fonts and Oniguruma WASM to exact
upstream package files by byte equality. Geist 1.800 is OFL-1.1, Symbols Nerd
Font 3.4.0 has the upstream MIT notice, KaTeX fonts retain their OFL-1.1 notices
in `licenses/OFL.KaTeX.txt` and `licenses/KaTeX-FONT-NOTICES.txt`, and Codicons are CC-BY-4.0. Their license
texts and embedded font metadata are in `licenses/`.

This repository distributes the built, modified client and the change summary
above. It does not contain the original downstream build checkout or its patch
history. To produce a stock client, check out the upstream commit, use the Node
and pnpm versions specified there, run `pnpm install --frozen-lockfile`, then
`pnpm build:web`. Reapplying downstream changes requires a new implementation;
that command does not reproduce this modified snapshot.

To replace the snapshot, copy a complete `out/web/` build into `dist/`, retain
all upstream and third-party notices, refresh this license inventory against the
new lockfile, then update `VERSION`. Keep `web-index.html` ending in `</body>`
for the return-widget injection. The supplied verifier checks the tree pin.

## Optional local-browser services

Local browser render (slot manager + external proxy pool) and grab (a page-injected
bridge) need extra services this repo does not install. Without them, it uses
the fallback behavior described above. An independently installed slot manager
listens on loopback port 18929;
its configuration lives at `/opt/orca-web/orca-local-browse.json`. The Orca gate exposes
that file and the manager's five public operations under `/orca-web/`, for the owner
only. Internal manager endpoints remain inaccessible through this route.

Sidecar integrations use the existing owner-gated routes.
