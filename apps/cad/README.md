# CAD

Drawing library at `/cad/`, local drawings in `~/cad/<project>/*.dxf`.
Removing the app keeps these drawings. DWG import uses the pinned CADCraft CLI
with a 60-second conversion timeout. The editor and parent save bridge share
one origin; the editor URL includes `?host=parent`.

`VERSION` pins Release asset URLs and SHA-256 hashes. Installation checks both
assets before replacing files. `web-bundle/VERSION` pins the extracted web files;
matching installed web/CLI bytes skip their downloads. Re-add leaves identical
files alone, and a download or checksum failure keeps the installation intact.
Public Release assets download without credentials. If installing a development
snapshot that still points at a private repository, `GH_TOKEN` may supply read
access; credentials are used only for downloads and never written to app files.

The current CLI asset is Linux x86_64 only. Linux aarch64 conversion remains
unmeasured and has no pinned CLI; installation reports the missing asset.

`web-bundle/` contains the Hangul build recipe, patches and the unmodified
upstream CLI source/build provenance (the web and CLI use different source commits). `licenses/` includes
upstream code and font notices, installed under `/cad/app/licenses/` beside
the served editor. See `web-bundle/README.md` for provenance and rebuilding.

Run backend regression tests in the disposable validation container:

```sh
CAD_TEST_CLI=/opt/stationdesk/runtime/cad/cadcraft-cli python3 -m unittest discover /opt/stationdesk/apps/cad -p test_backend.py -v
```
