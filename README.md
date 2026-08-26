# cmrelay

Install a [chatmail](https://chatmail.at) relay from one file.

Copy `cmrelay.run` to a fresh machine, run it,
and that machine becomes a working chatmail relay:
postfix, dovecot, filtermail, OpenDKIM, nginx,
unbound, chatmaild, acmetool, mtail, iroh-relay
and chatmail-turn, all unpacked into `/opt/cmrelay`
with systemd units registered and services running.

    ./cmrelay.run install --mail-domain relay.example.org

Or, if you would rather not move a 180 MiB file around by hand:

    pip install cmrelay
    cmrelay install --mail-domain relay.example.org

A pip-installed cmrelay is the CLI and nothing else.
The relay itself is a 650 MiB Nix closure,
and the installer has to run on the interpreter
from inside that closure, not on whatever pip installed it into.
So it downloads the payload matching its own version and hands over to it,
which puts you on exactly the path that is tested. `cmrelay download` fetches
the payload without installing it, for a host with no outbound access.

No apt, no pyinfra, no ssh orchestration, and nothing required
of the host beyond a kernel, systemd and a POSIX shell.
Not even a Python interpreter: the installer brings its own.

What makes that portable is Nix. The stack is built as a Nix
closure that carries its own glibc, so one file serves Debian 12,
Debian 13 and Fedora instead of needing a build per distribution.

> [!WARNING] This is experimental. It is for throwaway containers
> and test hosts, not for migrating a production relay.
> `cmdeploy run` in [chatmail/relay](https://github.com/chatmail/relay)
> remains the supported path, and is also the rollback.


## What it is not

cmrelay lands *beside* `cmdeploy run`, not on top of it.
They cannot share a host, because both want ports 25, 143, 465, 587 and 993,
so side by side means side by side in tooling rather than on a machine.

cmrelay does not fork relay and does not become a second place
where configuration lives. It vendors a pinned relay commit
and derives its configuration and systemd units from it by script,
so a cmrelay release names an exact relay commit,
and updating means re-running that script.


## Commands

| | |
|---|---|
| `cmrelay install --mail-domain <domain>` | fresh hosts only; refuses and modifies nothing if anything is in the way |
| `cmrelay upgrade` | convert an existing `cmdeploy` host; the only verb that may touch existing services |
| `cmrelay status` | units, versions and conflicts; modifies nothing |
| `cmrelay uninstall` | remove `/opt/cmrelay` and `/nix`, and unmask what `upgrade` masked |
| `cmrelay download` | fetch the payload for this version without installing it |
| `cmrelay --version` | the runtime face of `vendor.lock`, and what a bug report should paste |

Rolling back is two steps, and the first one is not optional:

    cmrelay uninstall
    cmdeploy run

`systemctl enable` fails on a masked unit, so a `cmdeploy run` against
a host still carrying cmrelay's masks would not come back up.
Uninstall re-enables nothing itself; it only unmasks,
and leaves the enabling to `cmdeploy run`.


## Where things go

Everything cmrelay *defines* lives in `/opt/cmrelay`:
`chatmail.ini`, the rendered configuration
for every component, and the systemd unit files.
Everything holding live *state* stays where it already is.

```
/opt/cmrelay/
├── chatmail.ini            # source of truth, written once, never overwritten
├── config/                 # rendered config, one directory per component
├── systemd/                # an index: one symlink per unit cmrelay owns
├── state/disabled-units    # what upgrade masked, for uninstall to unmask
└── nix/store/              # the closure

/home/vmail/mail/<domain>   # mail and credentials
/etc/dkimkeys/              # the signing key
/var/lib/acme/              # certificates and the ACME account
```

Those last three are shared in place with whatever ran
before, never relocated and never copied.
That is what makes converting back to `cmdeploy run` cost nothing:
nothing was taken away, only which units are enabled changed.

Unit files are the exception to "everything
cmrelay defines lives in `/opt/cmrelay`":
they are written into `/etc/systemd/system` and indexed by symlink
from `/opt/cmrelay/systemd`, rather than the other way round.
`systemctl disable` removes a unit that is itself a symlink in the unit
search path, and cmrelay deliberately disables `chatmail-expire.service`
and `chatmail-fsreport.service` because their timers own them --
which under the obvious layout would delete those units outright.

`/nix` is a symlink to `/opt/cmrelay/nix`.
Store paths are baked into every binary as its ELF interpreter and RPATH,
and the store directory is an input to every derivation hash, so building
for a different prefix would mean compiling glibc and everything above it
from source. Keeping the paths and moving the bytes gets both properties:
binaries run unchanged, and uninstall is `rm /nix && rm -rf /opt/cmrelay`.


## Supported platforms

systemd, a Linux kernel, x86_64 or aarch64, a POSIX shell,
and a writable `/` so `/nix` can be created.
The host's libc is irrelevant, because the closure carries its own.

The units use nothing newer than systemd 229,
so no distribution still in support is below the floor:
the Debian family, the RHEL family, SUSE, Arch and Gentoo built with systemd.

Alpine is excluded, disqualified twice over by musl
and OpenRC, along with the other init systems.
Immutable and atomic distributions (Fedora CoreOS, Silverblue,
openSUSE MicroOS, Ubuntu Core) are out of scope for this release,
because `/nix` cannot be created on a read-only root.

NixOS is deliberately excluded.
It already has a store and its own declarative service model,
so this installer would work mechanically while fighting the platform.
The right NixOS story is a module consuming the same derivations.


## Building

    nix build .#payload        # the single file, in result/bin/
    nix build .#closure        # just the store tree
    nix develop                # a shell with pytest, ruff, makeself, quilt

Two layers of compression, and the split is deliberate:
the outer makeself archive is uncompressed, because its stub would
otherwise have to shell out to a decompressor the host may not have.
A bare Debian 12 container carries only `tar` and `gzip`.
The inner store tarball uses zstd, decompressed
by a static zstd that travels inside the archive.


## Vendoring: relay stays the source of truth

`cmdeploy-relay/` is a git submodule pinned to one relay commit.
`vendor/` is generated from it and is never hand edited.
`patches/` holds every intentional local delta.
That boundary is the point of the structure:
at any moment it is obvious which bytes came from relay and which are ours.

    scripts/vendor-sync         # copy the declarative surface into vendor/
    scripts/vendor-check        # verify vendor/ against vendor.lock (runs in CI)
    scripts/extract-from-relay  # port table and binary pins, parsed out of the AST

The mapping in `vendor.toml` is **exhaustive by construction**.
`vendor-sync` fails on any file in relay's config surface that no rule covers,
so upstream adding a component with new templates breaks the cmrelay build
instead of silently producing a relay that is missing a service.
Converting silent divergence into a build failure
is the most valuable thing that script does.

What a script cannot decide is how cmrelay should *realise* a new component:
which nixpkgs attribute, which systemd unit, which paths.
What it guarantees is narrower and still worth having:
nobody can miss the fact that a decision is due.

Every patch in `patches/` does the same thing -- it turns a path upstream
hardcodes to Debian's layout into a template variable, so cmrelay can point it
at a store path. Nothing there changes what a directive means.

`scripts/extract-from-relay` is a bridge. relay keeps its preflight port table
and its pinned binary URLs inside Python function bodies,
so cmrelay parses them out of the AST rather than importing pyinfra.
Every extractor asserts the shape it expects;
moving the port table behind a helper call yields
`port_services is not a list literal at line 519` rather than silent drift.
When relay lands declarative files for both, delete that module.

Updating cmrelay is then one of exactly two kinds of change:

- **cmrelay local** -- installer, nix expressions,
  patches, the units cmrelay authors. `vendor/` unchanged.

- **relay bump** -- `git submodule update`, re-run `vendor-sync`,
  review the resulting `vendor/` diff. Nothing else changes.


## Components

Three ways in, and the tree is arranged so you can tell which at a glance.

**Stock nixpkgs**, named only in `nix/closure.nix`:
postfix, nginx, unbound, fcgiwrap, opendkim, mtail, python3 and its packages.
Their *configuration* still arrives through `vendor/config/`, which is why
`vendor/config/opendkim/` exists while `nix/packages/opendkim.nix` does not.

**Pinned prebuilt binaries**, in `nix/fetch/`:
filtermail, chatmail-turn and iroh-relay.
Nothing is compiled; each is a `fetchurl`
with the sha256 relay already records, read out of the extracted pins.
Fetching rather than building means cmrelay ships byte-identical binaries
to what deployed relays run, and buys no exposure to whether
a given iroh release still compiles against a current rustc.

**Built here**, in `nix/packages/`:
dovecot (the chatmail fork with its quilt series), acmetool,
chatmaild and crypt-r. dovecot is the only substantial compile.


## Testing

    pytest                      # unit tests: no root, no container, fast

    cmlxc deploy-cmrelay cmr0   # a real relay in an incus container
    cmlxc test-mini cmr0
    cmlxc test-cmdeploy cmr0

The acceptance criterion is deliberately not a new test suite.
`cmlxc test-mini` and `cmlxc test-cmdeploy` already know how
to exercise a deployed relay over SMTP, IMAP and deltachat-rpc,
and they do not care how it was installed. Against a cmrelay container
on Debian 12 they currently pass 89 of 89 and 6 of 6.

That is also why unit names are a contract rather than a coincidence:
the suite tails `journalctl -u dovecot` and `-t postfix/smtpd`,
so renaming `dovecot.service` to `cmrelay-dovecot.service` would silently cost
the entire oracle. It is likewise why the preflight exists at all -- identical
unit names are exactly what makes a distribution's own packages collide.


## Developing

    uv pip install -e .
    pytest

`ruff check .` and `ruff format --check .`
gate CI, along with `scripts/vendor-check`.


## License

MIT, matching relay.
