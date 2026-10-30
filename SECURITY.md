# Security

StationDesk is intended for one owner on a private network.
The one exception is the Publish app: with no external target configured it publishes snapshots you choose
through Tailscale Funnel (`https://<machine>.<tailnet>.ts.net:10000/<slug>/`, or `:8443` when 10000 is taken),
reachable by anyone with the link. Funnel exposes only the dedicated snapshot directories, never the private share,
tells search engines not to index, and is retired when the app is removed. Configuring your own target in
`state/publish.env` opens no Funnel.
Please report vulnerabilities privately to the repository maintainers using GitHub security advisories.

The installer sets its sudo-capable owner as Tailscale operator so later Serve updates need no sudo. Undo with `sudo tailscale set --operator=`.

Secret drop files are private to the owner (directory 0700, files 0600); their 1800-second TTL deletes files on the next list/put access, not on a background timer.
Deleting a drop cannot erase a value already read or `export`ed into a shell, child process, or another copy.
