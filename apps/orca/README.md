# orca — browser Orca ADE behind the StationDesk owner gate

One browser tab attaches to this box's [**Orca ADE**](https://github.com/stablyai/orca)
(a git-worktree parallel-agent IDE) — **no local install**. Authentication is your
**Tailscale identity** (no password); the box **owner only** may enter.

```
browser ──https/WireGuard──▶ tailscale serve :19940
                              │  identity header injected by tailscale serve
                              ▼
                   127.0.0.1:19943  nginx owner gate  ── not the owner? 403
                              │  (WS-safe proxy: no X-Forwarded-*, which would
                              ▼   break orca's Origin check)
                   0.0.0.0:19942  orca serve  ── nft loopback-only (gate reaches it,
                              │                    the tailnet cannot)
                    ┌─────────┴──────────┐
              web client            runtime
              (web-index.html)      (owns the worktrees, terminals, agents;
                                     keeps running even if the client disconnects)
```

Install it from the app store (Built-in) or `stationdesk add orca`; remove it the same way.

## Serves the patched web client

This installs the vendor `orca serve` (the runtime) but serves a **patched web client**
vendored at `web-bundle/dist/` — not the raw client `orca serve` ships. The patch fixes
an upstream bug where terminals die permanently after a page reload (*"Local PTYs are
unavailable in the web client"*) and adds an automatic reconnect overlay when the runtime
restarts. nginx serves it at `/orca-web/` and 302-redirects the vendor client URL
(`/web-index.html`) to it, so bookmarks and the launcher both land on the patched client.
Provenance, patch summary, and how to refresh the bundle: `web-bundle/README.md`.
`bin/verify-web-bundle.sh` checks the bundle against `web-bundle/VERSION` before install.

**Zero-paste entry.** The launcher opens the gate root (`https://<box>:19940/`). A
document navigation there is 302-redirected to the patched client carrying the runtime
pairing, so opening Orca lands directly in the workbench with no pairing paste. A
WebSocket upgrade to `/` (the runtime RPC) is proxied to the backend instead. The pairing
is printed only to the runtime's `serve.log`; `ready.sh` (the unit's ExecStartPost)
copies it into the nginx include `state/orca/pairing.conf`. The pairing URL is stable
across restarts (fixed pairing code + persisted runtime key).

**Not deployed here (client code is inert without them):** the bundle also carries
local browser render (needs a slot-manager sidecar) and element-comment "grab" (needs a
page-injected bridge). StationDesk does not install those services, so the client falls
back to upstream behavior for them. The gate already routes the slot manager's five
public operations (loopback port 18929) for a separately installed sidecar.

## Why the loopback guard

`orca serve` has **no `--host` flag**, so it binds `0.0.0.0`. `guard.sh` (run by
`stationdesk-orca-guard.service`) loads an nft table that drops non-loopback traffic to
the backend port, so the **only** route in is `tailscale serve` → the nginx owner gate.
The same unit loads an AppArmor profile that lets Electron's sandbox open its user
namespace on Ubuntu 24.04 (`kernel.apparmor_restrict_unprivileged_userns=1`). Both are
applied when the unit starts (boot included) and removed when it stops, so removing the
app leaves nothing in the kernel or `/etc`. Orca requires the guard unit: if the guard
cannot start, Orca does not start.

## Runtime prerequisites (handled by `install.sh` and the units)

- **Xvfb + GTK/nss/gbm libraries** — orca is Electron (Chromium); even headless `serve`
  needs an X display and the GTK runtime. `install.sh` installs them via `apt` (with a
  t64/non-t64 fallback) and `stationdesk-orca-xvfb.service` runs a dedicated display `:59`.
- **The AppImage** is downloaded from `stablyai/orca` and **sha256-verified** (mismatch
  aborts). It is `--appimage-extract`ed because the FUSE mount fails under `systemd --user`.
- **Passwordless `sudo`** for `apt` (once), `nft` and `apparmor_parser` (each guard start).
- **x86_64 and ARM64 (aarch64)** — each architecture uses its own pinned upstream AppImage and SHA256. Apple Silicon runs the ARM64 build inside OrbStack Ubuntu; no Intel emulation is needed.
- **zlib1g-dev** — provides `libz.so`, required by the ARM64 AppImage extractor even when the runtime libraries are already installed.
- The orca daemon self-detaches into an `app-orca-*.scope`, outside the service's
  KillMode; `reap.sh` (ExecStopPost) reclaims exactly this invocation's daemon.

## Usage — add a project first

Opening a terminal on an empty screen fails with
`Local PTYs are unavailable in the web client`: with no project, the terminal falls
back to the `local` execution host (your laptop), which the browser has no PTY for.
Instead: **add a project** → host = **`Orca Server`** (this box) → pick a folder →
open the workspace → open terminals/agents **inside it** (they attach to the box's
runtime). The project list is browser-local state, so a fresh browser starts empty —
re-add the project; the runtime's worktrees are still there.

## Files

| File | Role |
|---|---|
| `install.sh` | runtime libraries · pinned AppImage download, verify, extract · fixed pairing code · pairing include placeholder |
| `units/` | `stationdesk-orca-xvfb` (display) · `stationdesk-orca-guard` (nft + AppArmor) · `stationdesk-orca` (`orca serve`) |
| `guard.sh` · `ready.sh` · `reap.sh` | guard up/down · wait for the listener and capture the pairing · reclaim the detached daemon |
| `nginx/serve.conf` | the gate: patched client at `/orca-web/`, vendor redirect, zero-paste root, Porthole |
| `smoke.sh` | gate health: backend reachable, owner 302, other/anonymous 403, patched client served + Porthole injected, vendor path redirected |
| `web-bundle/dist/` | the vendored **patched web client** (built dist) — see `web-bundle/README.md` |
