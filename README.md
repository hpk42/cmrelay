# cmrelay

Install and maintain a [chatmail](https://chatmail.at) relay from one file.

Copy `cmrelay-installer-<version>-x86_64-linux.run` to a fresh machine
and run it. It installs and nothing more: postfix, dovecot, filtermail,
OpenDKIM, nginx, unbound, chatmaild, acmetool, iroh-relay and
chatmail-turn, all unpacked into `/opt/cmrelay` with the `cmrelay` CLI
linked. Configuring the relay and starting its services is `cmrelay
deploy`'s job, so a working chatmail relay is two commands:

    ./cmrelay-installer-0.4.0-x86_64-linux.run
    cmrelay deploy relay.example.org

The installer is done after the first line and `cmrelay` is the tool:

    cmrelay deploy          # make this host match its configuration
    cmrelay dns             # what is still missing from your zone
    cmrelay status          # units, versions, conflicts
    cmrelay upgrade         # a newer signed release
    cmrelay uninstall relay.example.org

Two artefacts, one code path. Unpacking a 50 MiB archive happens once
per host per release; deploying happens every time configuration changes.
So the installer publishes the store, links the CLI and stops:
configuring is `cmrelay deploy`'s job, and the installer carries
no deployment logic of its own.

Or, if you would rather not move a 50 MiB file around by hand:

    pip install cmrelay
    cmrelay deploy relay.example.org

`pip` is the last step that needs the network.
The released wheels are per-architecture and carry the payload
inside them, so the relay install itself reaches nothing:
useful on a firewalled or air-gapped host, and the reason
the wheel is the size of the payload rather than a few kilobytes.

The CLI still only ever hands over. The relay is a Nix closure
and it has to run on the interpreter from inside it,
not on whatever pip installed the CLI into,
so a wheel-installed `cmrelay` execs the payload, which installs
first when needed and then runs the verb you typed, exactly as if
you had run the `.run` file with that verb yourself -- which is
the only path that is actually tested.

Installing and deploying never download software.
`cmrelay upgrade` does, only when asked, and names the URL before it does.

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
and derives its configuration, systemd units and DNS checks from it by script,
so a cmrelay release names an exact relay commit,
and updating means re-running that script.


## Commands

| | |
|---|---|
| `cmrelay-installer [-y]` | unpack into `/opt/cmrelay` and link the CLI; configuring is not its job |
|
| `cmrelay deploy [DOMAIN]` | make this host match its configuration; idempotent, fast, the workhorse |
| `cmrelay dns [--zonefile PATH]` | check the published records against what this relay needs |
| `cmrelay status [--json]` | units, versions, conflicts and drift; modifies nothing |
| `cmrelay upgrade [FILE]` | install a newer signed release, with a rollback that works |
| `cmrelay uninstall DOMAIN` | hand the host back, so `cmdeploy run` can take over |
| `cmrelay version` | cmrelay version, relay commit, component versions, trusted keys |

`DOMAIN` is needed only on a host with no `chatmail.ini` yet.
After that the domain is read from the ini
and a supplied one is *checked* rather than obeyed:
changing the mail domain strands every existing mailbox,
and retyping it on each deploy is the only way a typo could ask for that.
`deploy --force` is reserved for exactly that override and nothing else.


### deploy

    cmrelay deploy [DOMAIN] [--take-over] [--force] [--yes]
                   [--set KEY=VALUE] [--config PATH]
                   [--website-only] [--disable-mail | --enable-mail]
                   [--skip-dns-check] [--reconcile] [--dry-run]
                   [--verbose] [--quiet]

One verb for fresh hosts, re-deploys, take-overs
and interrupted installs alike. It classifies the host first,
and every row of that table is a normal code path rather than an exception:

| state | how it is recognised | what happens |
|---|---|---|
| `installed` | `/opt/cmrelay/chatmail.ini` and a populated store | re-deploy; the fast path |
| `partial` | `/opt/cmrelay` exists but the ini or the store does not | resume, then re-deploy |
| `cmdeploy` | this host runs chatmail and has no cmrelay ini | offer take-over |
| `foreign` | conflicting units or ports, no chatmail markers | refuse, modify nothing |
| `bare` | nothing in the way | fresh deploy |

`--disable-mail` is a standing decision rather than a flag on one run:
it is recorded, honoured by every later deploy,
reported by `status`, and cleared only by `--enable-mail`.

A deploy prints what changed and what was already right:

```
Relay
  Host        installed by cmrelay 0.4.0
  Domain      relay.example.org  (acme, /home/vmail/mail/relay.example.org)
  Version     cmrelay 0.4.0, relay cae03e2714

Preflight                                                            0.07s
    no conflicting units, no occupied ports, no existing store

Configuration                                                        0.15s
    84 files unchanged

System files                                                         0.01s
    nothing changed

Services                                                             0.06s
    22 units, all as intended

Verify                                                               0.02s
    DKIM key present, TLS material present, services running

nothing to do; relay.example.org is up to date (0.29s)
```

A no-op deploy makes no network request, issues no `systemctl` call
and takes under a second. That is an acceptance test, not an aspiration.


### upgrade

    cmrelay upgrade [FILE] [--check] [--rollback] [--auto]
                    [--source github|pypi] [--allow-unsigned] [--force] [--yes]

Generations, not snapshots. Store paths are content-addressed,
so installing a new closure adds paths and removes none:
the previous release stays complete and runnable
at no copying cost, and rollback is one rename.

```
/opt/cmrelay/generations/
├── 3 -> ../nix/store/<hash>-cmrelay-closure
├── 3.state/                  # chatmail.ini, www/, state/
├── 4 -> ../nix/store/<hash>-cmrelay-closure
└── current -> 4
```

The sequence is arranged so that everything before the store is published
modifies nothing a failure would leave broken, publishing is additive,
and the flip is atomic. A failure after the flip reports
and stops rather than rolling back by itself:
the previous generation is intact either way,
and an automatic rollback on a partial failure can turn one problem into two.

Without a file, both GitHub and PyPI are asked
and the highest version wins. Neither is authority:
whatever answers, the candidate is verified against the release keys
inside the installed closure before it is unpacked, let alone run.


## Signing

The trust root is the installed generation.
The release public keys ship inside the closure at `<closure>/release-keys/`,
and `upgrade` verifies any candidate against them
before the candidate ever runs. Verification is Ed25519 through
the closure's own openssl, over the whole `.run` file.

The signature is an envelope rather than 64 raw bytes,
so a host meeting a future scheme reports the algorithm by name
instead of crying tampering, and one release can carry several signatures:

```
cmrelay-signature-v1
alg  ed25519
key  SHA256:0f3a9c...
sig  bXlSZWxlYXNlU2lnbmF0dXJl...
```

Two keys ship from the first signed release on;
`release-keys/` is empty before it.
`ci.pem` is the key the release workflow signs with,
so **the trust root is GitHub plus whoever can cause
that workflow to run** -- weaker than a key held offline,
much stronger than nothing. `offline.pem` is the recovery key,
whose private half is never uploaded anywhere; it signs nothing routinely
and exists so that losing the GitHub account does not mean losing
every deployed relay's upgrade path. See `release-keys/README.md`.

The installer verifies nothing about itself.
The first installation has no trust root to check a signature against;
obtaining that file from somewhere trustworthy is the operator's job.

`--allow-unsigned` is the only way to install a locally built payload,
and it prints a paragraph rather than a line.


## Reporting every change

Every mutation goes through one module, `cmrelay/fsops.py`, and lands
in a journal. Files under `/opt/cmrelay` collapse into a count per component;
every file outside it is named individually, because a rewritten `nginx.conf`
is routine and a rewritten `/etc/systemd/system/postfix.service`
is something an operator may have to explain to somebody later.

That is checked mechanically rather than by review.
One test walks the AST of every module and fails
on any call that could write, remove, chmod or link outside `fsops.py`.
A second runs a whole deploy against a fake root and asserts that the set
of paths which differ is exactly the set the journal recorded --
which catches a mutation that went through `fsops` and forgot to name itself.

`--dry-run` is then a flag on the journal
rather than a branch in forty call sites.

cmrelay also records the sha256 of every file it
writes outside its own tree, in `state/written.json`.
On the next deploy a file matching neither the new render
nor the recorded hash was edited by hand:
for paths cmrelay owns it is overwritten,
the previous content kept beside it as `<file>.replaced`,
and the overwrite reported; for paths it does not own
(`/etc/systemd/journald.conf`) it is left alone and reported.


## Where things go

Everything cmrelay *defines* lives in `/opt/cmrelay`:
`chatmail.ini`, the rendered configuration
for every component, and the systemd unit files.
Everything holding live *state* stays where it already is.

```
/opt/cmrelay/
├── .staging/               # the closure, unpacked, before it is installed
├── chatmail.ini            # source of truth, written once, never overwritten
├── config/                 # rendered config, one directory per component
├── systemd/                # an index: one symlink per unit cmrelay owns
├── www/                    # your page source; seeded once, then yours
├── www.dist/               # the release's own pages, to diff against
├── generations/            # one per installed closure, plus `current`
├── state/                  # what was masked, what was written, the lock
└── nix/store/              # the closure

/home/vmail/mail/<domain>   # mail and credentials
/etc/dkimkeys/              # the signing key
/var/lib/acme/              # certificates and the ACME account
```

Those last three are shared in place with whatever ran
before, never relocated and never copied.
That is what makes converting back to `cmdeploy run` cost nothing:
nothing was taken away, only which units are enabled changed.

`src/cmrelay/paths.py` names every absolute path a deploy can reach.
That is what lets the test suite redirect all of them under a temporary tree
and run a real deploy without touching the machine it runs on.

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
for a different prefix would mean compiling glibc and everything above
it from source. Keeping the paths and moving the bytes gets both properties:
binaries run unchanged, and uninstall is `rm /nix && rm -rf /opt/cmrelay`.


## What deploy owns, and what it does not

`/opt/cmrelay/config/**` and the unit files are cmrelay's outright
and are regenerated every run. Everything else has a rule:

| path | what happens |
|---|---|
| `/etc/resolv.conf` | left alone once `nameserver 127.0.0.1` is first |
| `/etc/mailname` | content-compared write |
| `/etc/environment` | appended once, guarded |
| `/etc/aliases` | written only when absent |
| `/etc/systemd/journald.conf` | a guest file: a hand edit is reported, not overwritten |
| `/var/www/html` | a manifest; pages cmrelay no longer produces are removed, others left |
| `/usr/local/bin/*` | recorded, and removed only if cmrelay put them there |

cmrelay never walks the mailbox tree.
The only reads permitted on `mailboxes_dir` are a `stat` of the root
and a dirent count that stops at 10 000, because a production relay
holds hundreds of thousands of maildirs and the only thing worth knowing
before renaming a domain is the order of magnitude.


## Web pages

There is no build machine, so the page source lives on the relay:
`/opt/cmrelay/www/` is seeded from the release once and belongs
to the operator from then on. Editing `index.md` and running
`cmrelay deploy --website-only` is the whole workflow.

`/opt/cmrelay/www.dist/` always holds the installed release's
own version of those pages, so after an upgrade a deploy can name
which of yours have diverged and let you look. It never merges.

`www_folder` stays honoured, reinterpreted as a path on the relay host:
unset uses `/opt/cmrelay/www/`; `disabled` publishes nothing
and leaves `/var/www/html` alone; an existing directory is used,
applying cmdeploy's own `src/index.md` rule so a tree copied off
a laptop works unchanged; and a path that does not exist warns loudly
and skips, because an adopted `chatmail.ini` often names
a directory that only ever existed on somebody's laptop.


## Supported platforms

systemd, a Linux kernel, x86_64 or aarch64, a POSIX shell,
coreutils, root, and a writable `/` so `/nix` can be created.
The host's libc is irrelevant, because the closure carries its own,
and so is its `tar`: the payload brings one.

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

    nix build '.?submodules=1#payload'   # the single file, in result/bin/
    nix build '.?submodules=1#closure'   # just the store tree
    nix develop                          # a shell with pytest, ruff, quilt

`VERSION` is the release version. `nix/closure.nix` writes it
and the commit into `manifest.json`, and an installed host reads it back
from there, which is what gives `upgrade` something to compare against.

The `.run` file is a shell header with three blobs after it -- a static zstd,
a static tar, and the zstd-compressed store -- at byte offsets patched
into the header at build time. It slices them out of itself with `tail -c`
and `head -c` and streams the store straight through them,
so the only host tools it needs are the ones in coreutils.
That is why there is no makeself and no host `tar`:
makeself unpacks with the host's tar before any cmrelay code runs,
so a machine without it fails with `Extraction failed` and no way to say why.
The bundled tar is libarchive built with one format and no codecs,
which is 0.9 MiB rather than the 9.2 MiB it is
when it can also do zip, 7z, xz, bzip2 and OpenSSL digests.


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

Most patches turn a path upstream hardcodes to Debian's layout into a template
variable, so cmrelay can point it at a store path. One does more:
relay's `dns.py` and `remote/rdns.py` are pure functions over DNS
that were written to run over an ssh connection on a Debian host,
and `pycode-local-dns.patch` makes them run locally against `drill`.
Vendoring them rather than reimplementing them means
a relay bump that changes the zone shows up as a diff
to review rather than as two divergent zone generators.

`scripts/extract-from-relay` is a bridge. relay keeps its preflight port table
and its pinned binary URLs inside Python function bodies,
so cmrelay parses them out of the AST rather than importing pyinfra.
Every extractor asserts the shape it expects;
moving the port table behind a helper call yields
`port_services is not a list literal at line 502` rather than silent drift.
When relay lands declarative files for both, delete that module.

Updating cmrelay is then one of exactly two kinds of change:

- **cmrelay local** -- installer, nix expressions,
  patches, the units cmrelay authors. `vendor/` unchanged.

- **relay bump** -- `git submodule update`, re-run `vendor-sync`,
  review the resulting `vendor/` diff. Nothing else changes.


## Components

Three ways in, and the tree is arranged so you can tell which at a glance.

**Stock nixpkgs**, named only in `nix/closure.nix`:
postfix, nginx, unbound, fcgiwrap, ldns, python3
and its packages. The web pages are rendered with markdown and jinja2,
and the invite QR code is an SVG rather than relay's composited PNG:
drawing that needs Pillow, which costs 74 MiB of image codecs
for one picture per host. `ldns` is there for `drill`,
which `cmrelay dns` queries the authoritative nameservers with:
it is one 0.6 MiB store path against seven for bind's dnsutils,
and `drill -Q` prints what `dig +short` prints
for every record type the zone check compares.
Their *configuration* still arrives through `vendor/config/`,
which is why `vendor/config/unbound/` exists
while `nix/packages/unbound.nix` does not.

**Pinned prebuilt binaries**, in `nix/fetch/`:
filtermail, chatmail-turn and iroh-relay.
Nothing is compiled; each is a `fetchurl`
with the sha256 relay already records, read out of the extracted pins.
Each binary is stripped after that hash check,
so the pin still proves the download,
and building nothing buys no exposure to whether
a given iroh release still compiles against a current rustc.

**Built here**, in `nix/packages/`:
dovecot (the chatmail fork with its quilt series), acmetool,
the opendkim milter (without its Perl genkey), chatmaild and crypt-r.
dovecot is the only substantial compile.


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

The unit suite runs without root and without a container,
and that constraint is worth keeping. It covers each row of the classification
table, each cell of the domain-resolution table, the activate planner
against a unit-state snapshot, the ownership rules, the `www_folder` cases,
the migration selector, signature verification including an unknown algorithm
and the offline key alone, `query_dns` against recorded `drill` output,
release discovery with either source down, and the capped mailbox count.


## Developing

    uv pip install -e .
    pytest

`ruff check .` and `ruff format --check .`
gate CI, along with `scripts/vendor-check`.
