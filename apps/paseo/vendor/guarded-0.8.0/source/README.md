# Source for the guarded Paseo 0.8.0 archives

Upstream: [getpaseo/paseo v0.8.0](https://github.com/getpaseo/paseo/tree/b8e24677e12b226c7c38c1c3a40649daa9f1152f),
commit `b8e24677e12b226c7c38c1c3a40649daa9f1152f`, Apache-2.0.
StationDesk's modifications are AGPL-3.0-only. The full license texts are in
`../UPSTREAM-LICENSE` and `../LICENSE`; patched files carry modification notices.

`guard-source.patch` contains the editable TypeScript for atomic message-send
preconditions and exact schedule state transition/restore. It changes eleven
upstream source files. The original guard build checkout was unavailable; this
source was reconstructed from the shipped guard's JavaScript and declarations.
A fresh strict TypeScript build and AST comparison confirmed the original guard
behavior, and `verify-guards.mjs` exercises rejection, replay, ownership,
intervening writes and recovery at all four durable write boundaries.

The later StationDesk overlays were authored against the built JavaScript.
Their editable patchers and tests are in `../../../patches/`. `distribution.patch`
is the exact combined JavaScript/declaration delta after the guard build,
including the current overlays and modification notices. It preserves the
shipped runtime code rather than replacing it with newly formatted output.
The unused `schedule/service.js.candidate.mjs` build scratch file is omitted;
no shipped module refers to it.

`compiled-files.txt` lists the guard outputs to put into the original npm
archives before that delta. `ORIGINAL_SHA256SUMS` pins all seven registry inputs.
Only server, protocol and client are repacked. The other four archives are
copied unchanged. Package-level Apache/AGPL texts and a source notice accompany
the three modified archives; the server also carries its web dependency notices.
The web inventory in `../WEB-THIRD-PARTY-LICENSES.txt` records locked browser
inputs, including build and cross-platform dependencies. It distinguishes
original license texts from 15 explicitly identified supplements using exact
published declarations and pinned SPDX standard texts.
This snapshot contains no upstream or private Git history.

## Rebuild

Use an isolated build environment with Git, Python 3.12+, Node 22.23.4 and npm.
The source checkout must be clean at the commit above. Downloads include the
public upstream checkout and the lockfile's npm dependencies. Do not run the
rebuild in an installed Paseo prefix.

From the public StationDesk repository root:

```sh
git clone https://github.com/getpaseo/paseo.git /tmp/paseo-source
git -C /tmp/paseo-source checkout --detach b8e24677e12b226c7c38c1c3a40649daa9f1152f
bash apps/paseo/vendor/guarded-0.8.0/source/build.sh /tmp/paseo-source /tmp/paseo-bundles
```

`build.sh` applies the TypeScript source patch, installs the selected workspaces
with `npm ci --ignore-scripts` using the upstream lockfile, runs the upstream
protocol generator and TypeScript build commands, checks guard behavior, and
runs the small archive assembler. The assembler verifies registry input hashes,
overlays the compiled files, applies `distribution.patch`, and makes deterministic
archives (fixed tar owner, timestamp, ordering and gzip timestamp). The final
hash check compares every archive with the shipped `../SHA256SUMS`.

The output also contains `INSTALLED_SHA256SUMS`. The installer uses the two
manifests from the parent directory. The seven archive hashes pin these inputs;
transitive npm dependencies installed later by StationDesk can still contain
version ranges, so this is not a claim that the full installed dependency tree
is frozen.
