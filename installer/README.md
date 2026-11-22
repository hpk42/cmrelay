# The installer directory

This contains the complete machinery for building, signing and testing a cmrelay installer file.
None of it runs on a cmrelay host, apart from the installer file it produces.

| Path                | Content                                                 |
|---------------------|---------------------------------------------------------|
| `header.sh`         | the shell header every installer file starts with       |
| `scripts/`          | the commands to run, see below                          |
| `nix/`              | the Nix build of the closure and of the installer file  |
| `vendor/`           | what is copied from chatmail/relay: list and patches    |
| `test_installer.py` | tests for relocation and the header                     |


## Scripts

The numbered ones in the order of a release; each runs from any directory.

| Script                                      | Does                                                                     |
|---------------------------------------------|--------------------------------------------------------------------------|
| `scripts/1_build-installer`                 | runs `test_installer.py`, builds the installer into `build/installer/bin/` |
| `scripts/2_test-cmlxc [--full] [NAME...]`   | deploys the build into local incus containers and runs `cmlxc test-mini` |
| `scripts/2_test-distros [IMAGE...]`         | installs, deploys and uninstalls on fresh Debian 12, 13, Ubuntu 24.04, 26.04 |
| `scripts/2_test-takeover [NAME]`            | takes a cmdeploy relay over with the build and hands it back             |
| `scripts/3_sign-installer FILE [--key KEY]` | signs an installer, offering to generate a release key if there is none  |
| `scripts/verify-cmrelay-signature FILE`     | checks an installer's signature line against the release key it carries |
| `scripts/chore-sync-from-relay COMMIT`      | refreshes the files copied from chatmail/relay to that commit            |
| `scripts/chore-bump-nixpkgs`                | moves `flake.lock` forward and builds the installer                      |
| `scripts/failing-release`                   | builds an installer of version 99.0.0 whose deploy fails, for the test   |

The `2_test-` scripts need [Incus](https://linuxcontainers.org/incus/),
and `2_test-cmlxc` and `2_test-takeover` a cmlxc with the `deploy-cmrelay` command, which is on a branch until it is released:

    uv tool install git+https://github.com/chatmail/cmlxc@hpk/deploy-cmrelay-init

With `--full`, `2_test-cmlxc` also runs cmdeploy's own test suite through `cmlxc test-cmdeploy`
and takes the last container through an upgrade, a failing upgrade and an uninstall.


## The Nix build

| File                      | Role                                                                    |
|---------------------------|-------------------------------------------------------------------------|
| `flake.nix`, `flake.lock` | the flake: pins nixpkgs and names the packages                          |
| `nix/closure.nix`         | the closure: every package, the copied files and the `cmrelay` tool     |
| `nix/payload.nix`         | packs header, `bsdtar` and closure into the installer file              |
| `nix/relocate.py`         | rewrites `/nix/store` paths to `/opt/cmrelay/lib/store`                 |
| `nix/packages/`           | the packages built here instead of taken from nixpkgs                   |
| `nix/fetch-binaries.nix`  | filtermail with its mtail program, chatmail-turn and iroh-relay, fetched |

nixpkgs follows the stable branch `nixos-26.05`, and `flake.lock` pins one state of it.
A pinned state gets no security fixes by itself:
run `scripts/chore-bump-nixpkgs` before every release, and monthly otherwise.


## The installer file

`cmrelay-installer-<version>-<arch>-linux.run` consists of four parts, in this order:

| Part      | Content                                                                 |
|-----------|-------------------------------------------------------------------------|
| header    | a shell script that unpacks the rest                                    |
| bsdtar    | a static tar extractor                                                  |
| closure   | the chatmail relay stack and the `cmrelay` tool, compressed             |
| signature | one 101-byte line signing everything before it; absent in a local build |

Run as root, it unpacks into `/opt/cmrelay/lib/store`
and links the `cmrelay` command into `/usr/local/bin`.
Nothing is configured or started until `cmrelay init` and `cmrelay deploy`.


## Releases and their signature

On a `v*` tag, `.github/workflows/release.yml` runs all of CI,
then signs the installers that CI built on bb33, runs and verifies them, and publishes them.
Its job `sign` runs on a bb33 runner labelled `bb33-sign`, which gets the private key
only in a run of this workflow that a `v*` tag started (bb33's README, "Release signing").

Between releases `host/VERSION` on main names the next version with `.dev0`, such as `0.5.1.dev0`,
so that `cmrelay upgrade FILE` takes a build of main as newer than the release before it.
chatmail/workflows' make_new_release.py writes the tag's version there in the release commit.

| Piece            | Where                                                                              |
|------------------|------------------------------------------------------------------------------------|
| signature        | the last line of the installer file, exactly 101 bytes                             |
| its content      | `cmrelay-sig `, the base64 of a raw Ed25519 signature and a newline                |
| signing key      | a file on bb33, never on GitHub and never in the repository                        |
| verification key | `release-key.pem` in the repository and in every installed cmrelay                 |

`scripts/verify-cmrelay-signature`, which the manual shows in full, carries a copy of the verification key;
the release workflow verifies each signed installer with it, so a copy that still holds an old key fails the release.

An upgrade from a mirror or a USB stick is therefore as safe as one from GitHub;
the first install of a host checks no signature.

| Command                | Does                                         |
|------------------------|----------------------------------------------|
| `cmrelay upgrade FILE` | verifies signature before running the file   |
| `cmrelay status`       | prints the release signing key's fingerprint |
