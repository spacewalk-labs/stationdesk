# macOS setup app

Build on macOS 13 or later with the Apple command line tools installed:

```sh
bash product/installer/mac/build.sh /absolute/path/to/output
open '/absolute/path/to/output/StationDesk Setup.app'
```

The output contains the SwiftUI app and `StationDesk-Setup-mac.zip`. The app carries
`setup.sh` and a product-only `source.bundle`; it does not require Python on macOS.
OrbStack is obtained through its official download page, rather than bundled.
The app is ad hoc signed for internal testing, without notarization or App Store
registration. Its architecture matches the build machine.

Commands run asynchronously with argv, and original log lines are shown in a
collapsible section. Check has a 30 second deadline; preparation, login and
installation have a 30 minute deadline. Errors retain the failed action for Retry.
The installer only opens a Tailscale login URL on `login.tailscale.com`, and only
opens the workspace after `install` succeeds and returns an HTTPS address.
