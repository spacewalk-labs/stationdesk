# CADCraft web bundle

Pinned Hangul build **0.6.2-e707ab4-hangul-2**. `VERSION` records the three
unmodified distribution files and their SHA-256 checksums.

- CADCraft: <https://github.com/storytold/cadcraft>, commit
  `e707ab4d7cc10102e550ad4c5bcecef1084a9baa`, MIT OR Apache-2.0.
- Optional craft-fonts input: <https://github.com/storytold/craft-fonts>, commit
  `3d9ab3a132ed0eff457e424226a435a901da30d3`.
- `nanum-gothic.patch` includes NanumGothic Regular in the web font filter.
  The font comes from Debian's `fonts-nanum` package; its OFL notice is included
  in `../licenses/NanumGothic-OFL.txt` (upstream google/fonts `ofl/nanumgothic/OFL.txt`).
- Web font selection at this commit includes BIZ UDPGothic Regular and Noto
  Sans Arabic. Their copyright and OFL notices are in `../licenses/` with the
  original craft-fonts attribution list. Other optional fonts in that list
  are filtered out of this web build.
- `host-run.patch` exposes the control protocol through same-parent, same-origin
  `cadcraft:run` / `cadcraft:result` messages. Requests run on the editor frame;
  immediate results return without waiting on wasm threads. Non-immediate
  operations return `not immediate`. The editor also reports active document changes
  through `cadcraft:document`, so the drawing library can track save destinations.
- CADCraft's own licenses and NOTICE are retained in `../licenses/`.
  `WEB-Cargo.lock`, `WEB-ATTRIBUTION.md` and `WEB-THIRD-PARTY-LICENSES.txt`
  retain web source metadata and dependency notices, including the egui
  embedded font licenses.

## Rebuild

The included `Dockerfile` is the original build recipe. Run it in a disposable
builder, without personal drawings or credentials; installation never builds it:

```sh
docker build -t cad-web-build apps/cad/web-bundle
container=$(docker create cad-web-build)
docker cp "$container:/build/dist/web/." ./cad-web-output
docker rm "$container"
```

Package the resulting HTML, JavaScript and wasm as the Release web asset, then update
`../VERSION` URL and SHA-256 pins. `web-bundle/VERSION` records the original build inputs. The recipe pins source commits and the Rust image; distro packages
and downloaded build tools may still change, so this is a rebuild recipe,
not a promise of byte-identical output. The shipped hashes identify the actual
reviewed bytes. No received source scripts run during app installation.

## Linux x86_64 CLI provenance and source

The CLI is **unmodified upstream v0.6.2**, extracted from
[`cadcraft-0.6.2-linux-x86_64.deb`](https://github.com/storytold/cadcraft/releases/download/v0.6.2/cadcraft-0.6.2-linux-x86_64.deb),
not built by StationDesk. Its source commit is
[`00c7613f1af3c73f7157fa91d9fe2635c6f23c06`](https://github.com/storytold/cadcraft/tree/00c7613f1af3c73f7157fa91d9fe2635c6f23c06)
(the v0.6.2 tag), distinct from the web commit above. The upstream package and
extracted binary were compared by SHA-256:

```
cac8361d04e4a7ebf0ab98649738920848e0ac9e91cf4112f2a36321d46ca02c  cadcraft-0.6.2-linux-x86_64.deb
ca2b3491bd175c39d556ed2aa1253e28cf3b99828ef31a0976372696388c842c  usr/bin/cadcraft-cli
```

To recover the exact distributed CLI without installing the package:

```sh
curl -fLO https://github.com/storytold/cadcraft/releases/download/v0.6.2/cadcraft-0.6.2-linux-x86_64.deb
printf '%s  %s\n' cac8361d04e4a7ebf0ab98649738920848e0ac9e91cf4112f2a36321d46ca02c cadcraft-0.6.2-linux-x86_64.deb | sha256sum -c -
dpkg-deb -x cadcraft-0.6.2-linux-x86_64.deb extracted
```

To rebuild the CLI from editable source, use a disposable Linux x86_64 builder
with Rust 1.90 or newer, a C linker, Git and curl. The upstream
[`packaging/linux/package.sh`](https://github.com/storytold/cadcraft/blob/00c7613f1af3c73f7157fa91d9fe2635c6f23c06/packaging/linux/package.sh)
builds `cadcraft-cli` with `--release --locked` and strips it. The upstream
release includes the optional craft-fonts input pinned below. No StationDesk
patches apply to the CLI:

```sh
git clone https://github.com/storytold/cadcraft.git
cd cadcraft
git checkout 00c7613f1af3c73f7157fa91d9fe2635c6f23c06
git clone https://github.com/storytold/craft-fonts.git ../craft-fonts
git -C ../craft-fonts checkout 3d9ab3a132ed0eff457e424226a435a901da30d3
CRAFT_FONTS_DIR="$PWD/../craft-fonts" CRAFT_FONTS_REQUIRED=1 \
  cargo build --release --locked -p cadcraft-cli
strip target/release/cadcraft-cli
```

This describes the source build, not byte-identical output: the original
upstream compiler, system libraries and build environment also affect bytes.
`../licenses/CLI-Cargo.lock` and `CLI-ATTRIBUTION.md` retain the exact upstream
source metadata; `CLI-THIRD-PARTY-LICENSES.txt` retains dependency license texts.
The CLI embeds all native craft-fonts faces except the source's excluded Noto
Sans CJK SC. Their attribution and OFL notices are included beside the code
licenses. The unmodified `acadrust` 0.6.3 dependency is MPL-2.0; its corresponding
source is available from <https://static.crates.io/crates/acadrust/acadrust-0.6.3.crate>.
Linux aarch64 is not currently provided by StationDesk's CAD app.
