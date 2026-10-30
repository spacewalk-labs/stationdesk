# StationDesk decisions

- Name: StationDesk; a personal desk of apps, with Bench as the optional workbench.
- License: AGPL-3.0 with commercial licensing available from Spacewalk.
- Free core: one owner; collaborator features are outside the core.
- Public repository: product source and notices; setup packages are published as release assets.
- App identity: folder name; hub uses `hub/`, other apps use `apps/<id>/`; bundled examples fall back to `examples/<id>/`.
- Manifest: required `label`, `icon`, `port`; optional `serve`, `files`.
  Other keys are ignored. Ports are fixed by apps; add rejects collisions with other installed apps before running the installer.
- App contract: optional `install.sh`, `units/*.service`, `nginx/*.conf`, `web/`, icon.
  Units use `stationdesk-<id>[-*].service`; nginx files are combined per app.
  For own-port apps, `nginx/serve.conf` supplies locations for the separate owner-gated
  listener at `port + 1`; without it, that listener proxies to the app backend.
- Install scripts and units receive `STATIONDESK_ROOT`, `STATIONDESK_APP_ID`,
  `STATIONDESK_APP_DIR`, `STATIONDESK_PORT`, `STATIONDESK_SHARE`, `STATIONDESK_STATE`,
  `STATIONDESK_OWNER`, `STATIONDESK_FQDN`, and `STATIONDESK_PORT_<ID>` (uppercase, hyphens replaced by underscores).
- Unit values are injected as systemd Environment entries; scripts inherit the environment.
  Units receive only their own named port, so adding a later app does not change earlier units;
  scripts and nginx rendering can read the installed apps’ named ports.
- Extra installed paths use `files`; relative paths are rooted at StationDesk, `~` is expanded.
  Strings declare app-owned paths. `{path = "~/.agents/skills/share-docs", target = "share-docs"}`
  declares a symlink to an app-relative target: create only at an empty path, record only matching
  links, and remove only while its target still matches. Existing user files or other links survive.
  Reinstall upgrades old unconditional path records for these declarations before retiring them.
  User data must stay outside these paths and convention-owned web directories.
- Ledger: `state/installed.json`, schema 1, app `dir`, `commit`, `serve`, `artifacts`, `origin`, `kind`, `repo`, `ref`.
  `origin` is `builtin`, a Git URL (including an optional #ref), or an absolute folder path.
  `repo` records an engine-owned clone shared by its apps; the last removal deletes it.
  Folder apps use a content digest as `commit`; Git apps keep the Git commit.
  Links have `kind = link`, `label`, `url`, `origin = url`, and empty `artifacts`.
  Convention paths are discovered; only nonstandard paths need `files` declarations.
- Ledger writes use a temporary file and atomic replace. Mutating engine commands
  serialize through `state/engine.lock` using `flock`.
- Changed add retires old recorded artifacts absent from the new app contract, using G1/G2.
- Changed web trees replace their recorded directory so deleted pages disappear.
- Pending nginx deletion remains recorded until projection succeeds, allowing retry after restore.
- Repeated add: unchanged unit content and Git app diff means no file rewrite or unit restart.
- Projection: ledger order becomes a tile array in `web/hub/__stationdesk.json`.
  Tiles contain `id`, `kind`, `origin`, `label`, `icon`, `port`, `serve`, `path`; icons use `/<id>/icon.<ext>`.
  Projection renders the owner-gated site and maps own-port apps via `serve` to `port + 1` (removed on uninstall), and Tailscale Serve 443 to the hub loopback port.
  Projection also regenerates `state/box-facts.md`, including the notification CLI.
- G1: remove only recorded artifacts; reject symlink ancestors before deleting anything.
  Reason: prevent deleting user files or traversing a replaced directory into outside data.
- G2: keep old nginx fragments, replace, test, restore on failure, reload on success.
  Reason: invalid files left on disk can prevent nginx from starting after a reboot.
- G3 (installer stage): leave the app's own cgroup before changing its units.
  Reason: restarting the terminal or launcher must not kill the installer doing the restart.
- G4 (site stage): one server-level owner gate with explicit app includes.
  Reason: every app must stay behind the same owner check, including direct ports.
- G5 (app stage): same-origin writes and exact-origin CORS.
  Reason: another website must not issue authenticated writes through the owner's browser.

- nginx runs as the owner in `stationdesk-nginx.service`, bound only to loopback (hub 19920).
  Its config, PID, temporary files and logs are under `state/`; rendered site under `nginx/`.
  No root nginx include reads user-owned files. Existing system nginx is left alone;
  only a newly installed distro nginx is stopped and disabled during bootstrap.
  This replaced the original system-nginx bootstrap.
- Owner comes from Tailscale Self.UserID → User.LoginName, falling back to `state/box.toml` owner.
  Offline smoke injects this header on loopback; real access goes through Tailscale Serve.
- `doctor` runs app smoke scripts and owner/other/anonymous gate probes; zero checks fail.
- `update` fetches the core and each addon clone, merges only fast forward, then refreshes
  every installed app (excluding links), collecting installation failures.
- Bootstrap also sets the installing owner as Tailscale operator.
  Undo: `sudo tailscale set --operator=`. `doctor` checks HTTPS through Serve
  when the machine has a Tailscale hostname.

- Publish keeps its manager at hub `/publish/`, while shared files use the root of HTTPS 8000.
  Its hub tile opens the manager. Unpublish unlinks symlinks only; direct files and copies remain.
  Share data and the fileview database are not removal artifacts.

- Cards live in `state/cards.sqlite3`; producers publish atomically through
  `state/cards-spool/{tmp,new,bad}`. The collector runs every two seconds.
  Same group, run declaration and link merge; count increases, urgency and peek
  only increase. Changed title/body resets read state; resolves archives a group
  or card. A later event reopens the resolved card instead of adding one, unread
  with its alert and run offered again, so a restart loop is one card ×N; a card
  the owner archived stays closed.
  Read cards idle for 48 hours archive; data expires after 180 days.
- Engine app units use `OnFailure=stationdesk-alarm@%n.service`. `ExecStopPost`
  also observes abnormal exits before automatic restarts, and `ExecStartPost`
  emits recovery. Notification hooks cannot fail an app start. A final failure
  can increment the same group's count through both failure hooks.
- Porthole reads the inbox preview every 30 seconds. Unread count alone does not
  animate. Peek animates for 15 seconds (normal) or 90 seconds (urgent); closing
  it does not mark the card read. The first visit establishes a watermark.
  Optional `/accounts/status.json` is `{ "status": "normal" | "warning" | "critical" }`:
  warning animates at six seconds per orbit, critical at two; absent means no ring.
  Reduced motion disables animation.

## Port allocation

Built-in loopback: 19920–19999; Company: 21000–21999; Add-ons: 22000–22999 (conventions).
Reserve `port + 1` for own-origin gates and devterm ttyd; hub uses its own port and HTTPS 443.
`serve` and additional endpoint `https_port`/`gate_port` must also avoid other installed apps.
Add checks only the target against the ledger (older entries fall back to installed manifests) before its installer runs.
