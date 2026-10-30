# Graphical setup

The setup app uses the same `install.sh` and `stationdesk` engine as the terminal.
It installs the five essential apps and includes Paseo by default; uncheck Paseo
to pass `--no-paseo`. Add other apps later in StationDesk.
English is the default setup language; choose 한국어 in the labeled language menu for Korean.
Both setup windows and application launchers use the existing StationDesk O3 icon.

Ubuntu 24.04: open `stationdesk-installer_0.1.3_all.deb` in the system package
installer, install it, then open **StationDesk Setup** from the applications menu.
System package dependencies include Python/Tk and pkexec. Administrator permission
is requested by the OS authentication dialog. The app never collects passwords.
A desktop user session with systemd is required.

macOS 13+: unzip `StationDesk-Setup-mac.zip`, put **StationDesk Setup.app** in
Applications, and open it. The published Mac build targets Apple silicon (arm64). Building on an Intel Mac targets x86_64. OrbStack is
obtained from its official download page; setup creates or reuses only the
`stationdesk` Ubuntu 24.04 machine. No Python installation is required on macOS.
The signing status is stated in each release. Ad hoc builds are not notarized or App Store apps;
Gatekeeper may require **System Settings → Privacy & Security → Open Anyway** after the first launch attempt ([Apple’s instructions](https://support.apple.com/en-us/102445)). This does not change
macOS security settings automatically.

Both packages contain a history-free Git seed of the public source as the setup
entry point. Installation clones the public StationDesk repository into
`/opt/stationdesk`; `stationdesk update` follows its main branch with fast-forward
updates. Internet access is required for that checkout and vendor downloads.
Setup also installs Claude Code, Codex, OpenCode, and Antigravity when missing.
Their accounts and subscriptions are separate from StationDesk.

Build outputs must be outside `product/`:

```sh
bash installer/linux/build-deb.sh /absolute/output/stationdesk-installer_0.1.3_all.deb
# On a Mac with Apple command line tools:
bash installer/mac/build.sh /absolute/output
```

`setup.sh` accepts `check`, `prepare`, `login`, and `install [--no-paseo]`.
It streams original output and `SD_STATUS<TAB>key<TAB>value` status lines.
A zero exit from check/login is not installation success. Ready requires install
success, `stationdesk doctor`, an HTTPS request, and the resulting workspace URL.
Errors retain logs and expose Retry. User files and existing VMs are never deleted.
