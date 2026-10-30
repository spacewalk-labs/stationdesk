# StationDesk Paseo browser host

The original browser automation host and live panel are retained. `../install.sh`
owns app ports, provider config, systemd units and the user nginx owner gate.
`install.sh` here only stages the host at `$STATIONDESK_ROOT/runtime/paseo-browse-host`,
installs Playwright/Chromium and patches the served web UI using `PASEO_WEBUI_DIR`.
The service still speaks Paseo's native browser-host WebSocket protocol and serves
live browser panels through the owner-gated `/browse-view/` route.

The runtime uses `PASEO_WS_URL`, `PASEO_BROWSE_STREAM_PORT`,
`PASEO_BROWSE_ALLOWED_ORIGIN`, and the native `PASEO_HOST_*` variables. These
are the browser host's upstream protocol contract; StationDesk resolves them from
its app record. The port and origin have no baked-in company connection.

`bin/patch-web-ui.js` retains the exact current patch source and its pinned bundle
shape hashes: seventeen general edits and three browse edits. The general edits
include sidebar order sync, touch gestures, menus and archive handling. The
sidebar's persisted keys and `/airlock-ui-state/` wire route are retained so the
storage format and already-open clients remain identical to the source app.

Existing tests are in `test/` and `bin/patch-web-ui.test.js`. Browser tests and
`smoke.sh` run only in the disposable LXD installation.
