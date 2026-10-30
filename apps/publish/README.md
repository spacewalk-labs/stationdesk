# Publish

Publish provides uploads, search/filter/sort,
symlink unpublish, confirmed direct-file deletion, batch operations, broken-link
repair, and external HTML snapshots (open/password protected, duration, revoke,
refresh, linked-document bundles). The manager is owner-only at `/publish/`.
The document listener remains `:8000`; removing the app preserves shared data.
Uploads from the Publish manager are stored in the shared directory and appear in
its list. Card uploads are temporary files in `~/uploads`, cleaned after 24 hours.

The `/share-docs` tip above the document list explains how to ask Claude Code or
Codex to create and share a document. It opens on the first visit and remembers
its open/closed state in that browser. The list uses compact rows and 20 documents
per page; search, filters and sorting return to the first page. External links
whose source is missing remain manageable after the last page's source items.

Views: the `:8000` listener logs each document request to `state/publish/views.log`
(not assets or the root listing); the app counts 200/304 requests from browsers
(not curl, bots, HeadlessChrome) into `state/publish/views.json`, shown per row and usable
for sorting and the Views filter. Counting starts at install, and unpublishing resets a
document's count. Funnel snapshots are not counted.

The normal app ABI supplies `STATIONDESK_SHARE`, `STATIONDESK_STATE`,
`STATIONDESK_PORT`, `STATIONDESK_OWNER`, and `STATIONDESK_FQDN`.
External publishing works out of the box through Tailscale Funnel when no target is
configured in `state/publish.env`. Snapshots are served at
`https://<STATIONDESK_FQDN>:10000/<slug>/` (gated ones at `/g/<slug>/`); the installer
falls back to `:8443` when 10000 is taken and never uses 443, which is the hub. Funnel
exposes only the dedicated snapshot directories under `state/publish/` (never the private
share directory), directory listings are 404, and search engines are told not to index
(`X-Robots-Tag: noindex, nofollow` by default; see the per-page switch below). This is the one place StationDesk
makes something reachable from the public internet; it is limited to documents you
publish, and removing the app retires the mapping. If the tailnet policy does not grant
this machine the `funnel` attribute, install still succeeds and the Publish page says so
with a link to the [Tailscale admin console](https://login.tailscale.com/admin/acls); once
it is allowed, run `stationdesk add publish` again. Funnel is brought up by the engine's
endpoint mechanism (`endpoints` in `app.toml`, `funnel: true`).

To use your own target instead, put it in `state/publish.env` (keep it mode 0600; see
[examples/publish.env](examples/publish.env)) and restart `stationdesk-publish`. Any
`PUBLIC_MODE`, `INGEST_URL` or `BASE_URL` there means "configured": no Funnel is opened,
and a Funnel from an earlier install is retired on the next `stationdesk add publish`. The
default document URL is `https://<STATIONDESK_FQDN>:8000`;
`STATIONDESK_PUBLISH_DOC_URL` overrides it. Set
`STATIONDESK_PUBLISH_TITLE_META=true` to enable optional HTML title metadata.

For a local target, provide separate public, gated, and password directories and
install `apache2-utils` (the `htpasswd` command). Include the supplied
[public-server.conf](examples/public-server.conf) in your own user nginx HTTP
configuration and supply your public DNS/TLS forwarding separately. The example
listens on loopback and opens no public port. It deliberately keeps all public
snapshots outside the private share directory. A remote target instead uses your
ingest URL, base URL, and token environment variable; the receiver can declare
bundle/gated capabilities through its health contract. No company service is a default.
Gated snapshots need `apache2-utils` in either case.

The list shows each document's own `<title>` (from the first 256 KB of the file) as the row heading,
with the file name and project below it. Search matches title, file name and project; the filter
chips (type, status, project, modified) allow several picks, OR within a row and AND across rows.

Each published page has its own search-engine switch in the Share popover, off by default. On a
Funnel target, turning it on writes a `.indexable` file into that snapshot's directory and the
Funnel server then answers that page without `noindex` (`robots.txt` allows crawling, the header
decides per page). Password pages never take it. With your own web server or a remote ingest
service the switch is disabled and says why: those pages stay as that server serves them.

Site name and favicon: set `STATIONDESK_PUBLISH_SITE_NAME` and `STATIONDESK_PUBLISH_FAVICON`
(an image file, up to 256 KB) in `state/publish.env` and restart `stationdesk-publish`. They show
in the manager's header and tab, and the favicon is added inline to externally published pages
that have no `<link rel="icon">` of their own; a page's own icon is kept. Defaults are the
app's own name and icon. The app never writes this file: the manager's Settings view is
read-only and offers a "Copy prompt" button for asking your agent to change it.

To serve published pages from your own domain (for example `docs.example.com`), have your
coding agent follow [docs/custom-domain.md](docs/custom-domain.md): Funnel cannot serve custom
domains, so it covers a Cloudflare Tunnel (recommended; the box stays the origin) and Cloudflare
Pages (with what breaks), plus rolling back to Funnel. The manager's Settings view has the prompt
ready to copy; nothing is automated.

Expiry and upload cleanup run at startup and every 60 seconds without a browser
visit. `python3 server.py --cleanup` runs the same cleanup once. A snapshot can
remain accessible until the next sweep.
The manager's mutations require the owner identity and an Origin matching Host.

From the public repository root, run
`python3 -m unittest discover tests -p 'test_publish*.py'` and
`node apps/publish/test-bundle-plan-state.mjs` for regressions. Run live
API/nginx installation checks only in a disposable environment.
