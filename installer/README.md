# Install StationDesk

[Download setup](https://github.com/spacewalk-labs/stationdesk/releases/latest) · [Source repository](https://github.com/spacewalk-labs/stationdesk) · [Project README](../README.md)

Use the release assets, rather than GitHub's source-code ZIP. English is the default
setup language; choose 한국어 in the language menu for Korean. An internet connection
is needed. The app never collects your administrator or Tailscale password.

## Mac (Apple silicon)

The setup app itself supports macOS 13+, but current OrbStack requires macOS 14+.
The published v0.1.3 app targets Apple silicon (arm64).

1. Download [StationDesk-Setup-mac.zip](https://github.com/spacewalk-labs/stationdesk/releases/download/v0.1.3/StationDesk-Setup-mac.zip), unzip it, and move **StationDesk Setup.app** to Applications.
2. Open it once. The current release is ad hoc signed, not notarized, and distributed outside the Mac App Store. If macOS blocks it, dismiss the alert without deleting the app. Go to **System Settings → Privacy & Security → Open Anyway**, confirm and authenticate on your Mac if requested. Approve only the app from the release above. [Apple's instructions](https://support.apple.com/en-us/102445); wording varies by macOS version.
3. Choose **Get started**. If OrbStack is missing, use **Download OrbStack**, install it from the [official site](https://orbstack.dev/download), and open it to complete its own setup. OrbStack has separate licensing terms.
4. Return to setup, choose **Check again**, then **Prepare workspace**. Setup creates or reuses only the Ubuntu 24.04 machine named `stationdesk`. When prepared, choose **Continue**. Other existing VMs are not used.

No Python installation is required on macOS. StationDesk runs inside the Ubuntu machine.

## Ubuntu 24.04 desktop

1. Download [stationdesk-installer_0.1.3_all.deb](https://github.com/spacewalk-labs/stationdesk/releases/download/v0.1.3/stationdesk-installer_0.1.3_all.deb).
2. Open it with your graphical package installer and choose **Install**. If it opens in an archive viewer, right-click and choose your software/package installer. Approve the OS administrator dialog.
3. Open **StationDesk Setup** from the applications menu. Choose **Get started → Prepare**, approve the OS authentication dialog, and choose **Continue** when ready.

A desktop user session with systemd is required. Package dependencies include Python/Tk
and pkexec. OrbStack is not needed on Ubuntu.

## In either setup app

1. **Choose apps.** StationDesk, Terminal, File Viewer, Publish and Dev Monitor are included. Paseo is selected by default; uncheck it to skip it. Add other apps later in StationDesk.
2. **Connect Tailscale.** Choose **Open Tailscale sign-in**, sign in on the official page, approve the StationDesk machine, and return to choose **Check sign-in**. No StationDesk account is needed.
3. **Install.** Choose **Install StationDesk** and keep the machine online while components download. If a step fails, the app shows the error and offers **Retry**.
4. **Open.** On the Ready screen choose **Open StationDesk**. On your browser device — including the Mac hosting OrbStack — [install Tailscale](https://tailscale.com/download) and sign in as the **same personal account that owns the workspace machine**. Save the displayed HTTPS address. The workspace machine must stay running.

Setup also installs missing Claude Code, Codex, OpenCode and Antigravity tools.
Sign in to the agents you use after setup; their accounts and subscriptions are separate.

## Troubleshooting

- **No Open Anyway button on Mac:** try opening the app again, then return to Privacy & Security. A managed Mac may require your administrator's approval. Follow Apple's linked instructions; no system-wide security changes are required by setup.
- **Workspace address will not open:** check that the Ubuntu machine is running and both it and the browser device are connected to Tailscale as the same personal owner. A fleet-tagged device can have a different identity from your personal account.
- **Need help:** [open an issue](https://github.com/spacewalk-labs/stationdesk/issues) with your OS, release and relevant error. Remove secrets and private details before posting. Use [private security reporting](../SECURITY.md) for vulnerabilities.

## Build and implementation notes

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
