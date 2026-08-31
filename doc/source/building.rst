How cmrelay is built
====================

This section describes how the single-file installer comes about
and how developers can work on cmrelay.
Operators do not need any of it to run a relay;
:doc:`getting_started` covers usage,
and :doc:`overview` the deployed architecture.

cmrelay produces two release artefacts with one code path:
the ``cmrelay-installer-<version>-<arch>-linux.run`` file
published on GitHub, and per-architecture wheels published on PyPI
which carry that same ``.run`` file inside them.
Unpacking the archive happens once per host per release;
deploying happens every time configuration changes.
So the installer publishes the software, links the CLI and hands
over to ``cmrelay deploy``, and carries no deployment logic of its own.


The Nix closure
---------------

What makes one file serve Debian, Fedora, SUSE and Arch alike is Nix.
The whole relay stack is built as a *Nix closure*:
one self-contained ``/nix/store`` tree holding every component
together with everything it links against,
down to its own glibc and its own Python interpreter.
The host contributes a kernel, systemd, a POSIX shell and coreutils,
and nothing else; the host's libc and package manager are never involved.

:repofile:`nix/closure.nix` assembles the closure.
Its output is deliberately not a merged ``bin/`` tree but a manifest,
``manifest.json``, naming each component's store path and version:
components like Postfix and Dovecot resolve sibling programs
relative to their own prefix and must keep it,
and ``cmrelay version`` and a bug report want to know
exactly which store path a binary came from.
The manifest also records the release version and the git commit,
which is what gives ``cmrelay upgrade`` something to compare against.

Store paths are baked into every binary
as its ELF interpreter and RPATH,
and the store directory is an input to every derivation hash,
so building for a different prefix would mean compiling glibc
and everything above it from source.
cmrelay instead keeps the paths and moves the bytes:
the store lands under ``/opt/cmrelay/nix``
and ``/nix`` is created as a symlink to it.
Binaries run unchanged, everything stays in one directory,
and uninstall is ``rm /nix && rm -rf /opt/cmrelay``.

The closure is *not* a Nix store in the operational sense:
the host has no Nix, nothing verifies or rebuilds paths,
and files a relay never reads
(headers, static libraries, manpages, locales)
are stripped on the way into the payload.


The single-file installer
-------------------------

The ``.run`` file is a shell header with three blobs after it,
at byte offsets patched into the header at build time::

    [ header ][ zstd ][ tar.zst ][ store.tar.zst ]

The header, :repofile:`stub/header.sh`, slices the blobs
out of its own file with ``tail -c`` and ``head -c``
and streams the store archive through the bundled zstd and tar,
so the only host tools it needs are the ones in coreutils.
The bundled tar is libarchive built with one format and no codecs,
under one MiB instead of the nine it costs
when it can also do zip, 7z, xz and bzip2.
Bringing both tools along is what lets the installer run
on a container image that has no ``tar`` of its own.

Extraction goes to a staging directory first,
and the store is published only after the operator has agreed,
which is what keeps "modified nothing" true of a refused install.

A store binary names an ELF interpreter that does not exist
until the store is published, so the header cannot execute
the bundled Python directly: it invokes the dynamic linker
with the interpreter as an argument, which bypasses ``PT_INTERP``.
From there the ``cmrelay installer`` verb takes over
and everything else is ordinary Python.

:repofile:`nix/payload.nix` builds the file:
it packs the closure with reproducible tar settings,
compresses it with a long-range zstd window
(the closure repeats itself across store paths),
and patches the four byte counts into fixed-width header fields,
so that patching cannot change the size of the header
the offsets are measured from.


The wheel
---------

``pip install cmrelay`` is meant to be the last step
that needs the network, so the ``.run`` file travels *inside* the wheel
rather than being fetched on first use.
That makes the wheel per-architecture
and roughly the size of the payload.

A pip-installed ``cmrelay`` reaches only a handful of wheel-side
modules and then execs the payload, which brings its own interpreter;
the wheel declares no dependencies for the same reason.
The relay is a Nix closure and has to run
on the interpreter from inside it,
not on whatever pip installed the CLI into,
so everything proceeds exactly as if you had copied
the ``.run`` file across yourself,
which is the only path that is actually tested.

:repofile:`scripts/wheel-cmrelay.py` builds the wheel
using nothing but the standard library, so CI can run it
directly after ``nix build``;
:repofile:`nix/wheel.nix` is the convenience wrapper
for building one locally.


Generations and upgrades
------------------------

Store paths are content-addressed,
so installing a new closure adds paths and removes none:
the previous release stays complete and runnable
at no copying cost, and rollback is one rename. ::

    /opt/cmrelay/generations/
    ├── 3 -> ../nix/store/<hash>-cmrelay-closure
    ├── 3.state/                  # chatmail.ini, www/, state/
    ├── 4 -> ../nix/store/<hash>-cmrelay-closure
    └── current -> 4

An upgrade is sequenced so that everything before the store
is published modifies nothing a failure would leave broken,
publishing is additive, and the flip of ``current`` is atomic.
A failure after the flip reports and stops
rather than rolling back by itself:
the previous generation is intact either way,
and an automatic rollback on a partial failure
can turn one problem into two.

Upgrades rarely need logic of their own.
``deploy`` regenerates every config file and every unit from scratch,
so a changed template, a new service and a dropped service
all need nothing; explicit migrations exist only for the narrow cases
a regeneration cannot express, such as a moved path or a renamed unit,
and each one is written to be safe to re-run.

Without a file, ``cmrelay upgrade`` asks both GitHub and PyPI
and the highest version wins. Neither is authority:
whatever answers, the candidate is verified against the release keys
inside the installed closure before it is unpacked, let alone run,
so a compromised download source can waste bandwidth and nothing else.


Release signing
---------------

The trust root is the installed generation.
The release public keys ship inside the closure
at :repodir:`release-keys`, and ``upgrade`` verifies any candidate
against them before the candidate ever runs.
Verification is Ed25519 through the closure's own openssl,
over the whole ``.run`` file.

The signature is an envelope rather than 64 raw bytes,
so a host meeting a future scheme reports the algorithm by name
instead of crying tampering,
and one release can carry several signatures::

    cmrelay-signature-v1
    alg  ed25519
    key  SHA256:0f3a9c...
    sig  bXlSZWxlYXNlU2lnbmF0dXJl...

Two keys ship from the first signed release on.
``ci.pem`` is the key the release workflow signs with,
so the trust root is GitHub plus whoever can cause
that workflow to run: weaker than a key held offline,
much stronger than nothing.
``offline.pem`` is the recovery key,
whose private half is never uploaded anywhere;
it signs nothing routinely and exists so that losing
the GitHub account does not mean losing
every deployed relay's upgrade path.

The installer verifies nothing about itself.
The first installation has no trust root
to check a signature against; obtaining that file
from somewhere trustworthy is the operator's job.
``cmrelay upgrade --allow-unsigned`` is the only way
to install a locally built payload,
and it prints a paragraph rather than a line.


Vendoring: relay stays the source of truth
------------------------------------------

cmrelay does not fork the
`chatmail relay repository <https://github.com/chatmail/relay>`_
and does not become a second place where configuration lives.
It vendors a pinned relay commit and derives its configuration,
systemd units and DNS checks from it by script,
so a cmrelay release names an exact relay commit,
and updating means re-running that script.

- :repodir:`cmdeploy-relay` is a git submodule
  pinned to one relay commit.

- :repodir:`vendor` is generated from it and is never hand edited.

- :repodir:`patches` holds every intentional local delta.

That boundary is the point of the structure: at any moment
it is obvious which bytes came from relay and which are cmrelay's own. ::

    scripts/vendor-sync         # copy the declarative surface into vendor/
    scripts/vendor-check        # verify vendor/ against vendor.lock (runs in CI)
    scripts/extract-from-relay  # port table and binary pins, parsed out of the AST

The mapping in :repofile:`vendor.toml` is exhaustive by construction.
``vendor-sync`` fails on any file in relay's configuration surface
that no rule covers, so upstream adding a component with new templates
breaks the cmrelay build instead of silently producing
a relay that is missing a service.
What a script cannot decide is how cmrelay should *realise*
a new component: which nixpkgs attribute, which systemd unit,
which paths. What it guarantees is narrower and still worth having:
nobody can miss the fact that a decision is due.

Most patches turn a path upstream hardcodes to Debian's layout
into a template variable,
so cmrelay can point it at a store path. One does more:
relay's ``dns.py`` and ``remote/rdns.py`` are pure functions over DNS
that were written to run over an ssh connection on a Debian host,
and ``pycode-local-dns.patch`` makes them run locally
against ``drill``. Vendoring them rather than reimplementing them
means a relay bump that changes the zone shows up
as a diff to review rather than as two divergent zone generators.

``scripts/extract-from-relay`` is a bridge.
relay keeps its preflight port table and its pinned binary URLs
inside Python function bodies, so cmrelay parses them
out of the AST rather than importing pyinfra.
Every extractor asserts the shape it expects,
so moving upstream code around yields a build error
rather than silent drift.

Updating cmrelay is then one of exactly two kinds of change:

- **cmrelay local**: installer, nix expressions,
  patches, the units cmrelay authors. ``vendor/`` unchanged.

- **relay bump**: ``git submodule update``, re-run ``vendor-sync``,
  review the resulting ``vendor/`` diff. Nothing else changes.


Components
----------

Components enter the closure in one of three ways,
and the tree is arranged so you can tell which at a glance.

**Stock nixpkgs**, named only in :repofile:`nix/closure.nix`:
postfix, nginx, unbound, fcgiwrap, ldns, python3 and its packages.
The web pages are rendered with markdown and jinja2,
and the invite QR code is an SVG rather than relay's composited PNG:
drawing that needs Pillow,
which costs 74 MiB of image codecs for one picture per host.
``ldns`` is there for ``drill``,
which ``cmrelay dns`` queries the authoritative nameservers with.
The *configuration* of these components still arrives
through :repodir:`vendor/config`, which is why
``vendor/config/unbound/`` exists
while ``nix/packages/unbound.nix`` does not.

**Pinned prebuilt binaries**, in :repofile:`nix/fetch/binaries.nix`:
filtermail, chatmail-turn and iroh-relay.
Nothing is compiled; each is a ``fetchurl`` with the sha256
relay already records, read out of the extracted pins.
Each binary is stripped after that hash check,
so the pin still proves the download,
and building nothing buys no exposure to whether a given release
still compiles against a current toolchain.

**Built here**, in :repodir:`nix/packages`:
dovecot (the chatmail fork with its quilt patch series), acmetool,
the opendkim milter (without its Perl key generator),
chatmaild and crypt-r.
dovecot is the only substantial compile.

mtail is deliberately absent:
the operator installs it, and the preflight prints
the release relay pins when ``mtail_address`` asks for it.

The units cmrelay authors itself live in :repodir:`units`,
for the daemons Debian supplies units for and nixpkgs does not.
Unit names are a contract:
``dovecot.service``, ``postfix.service`` and the rest keep exactly
the names Debian uses, because relay's own code
and the acceptance suite refer to them,
and ``journalctl -u dovecot`` is how the tests read logs.
This is also why the deploy preflight exists at all:
identical unit names are exactly what makes
a distribution's own packages collide.


How deploy reports changes
--------------------------

Every mutation a deploy makes goes through one module,
:repofile:`src/cmrelay/fsops.py`, and lands in a journal.
Files under ``/opt/cmrelay`` collapse into a count per component;
every file outside it is named individually,
because a rewritten ``nginx.conf`` is routine
and a rewritten ``/etc/systemd/system/postfix.service``
is something an operator may have to explain to somebody later.

That is checked mechanically rather than by review.
One test walks the AST of every module and fails on any call
that could write, remove, chmod or link outside ``fsops.py``.
A second runs a whole deploy against a fake root
and asserts that the set of paths which differ is exactly
the set the journal recorded,
which catches a mutation that went through ``fsops``
and forgot to name itself.
``cmrelay deploy --dry-run`` is then a flag on the journal
rather than a branch in forty call sites.

:repofile:`src/cmrelay/paths.py` names every absolute path
a deploy can reach. That is what lets the test suite redirect
all of them under a temporary tree and run a real deploy
without touching the machine it runs on.


Building and developing
-----------------------

::

    nix build '.?submodules=1#payload'   # the single file, in result/bin/
    nix build '.?submodules=1#closure'   # just the store tree
    nix develop                          # a shell with pytest, ruff, quilt

:repofile:`VERSION` is the release version.
``nix/closure.nix`` writes it and the commit into ``manifest.json``,
and an installed host reads it back from there.

For working on the Python code without Nix::

    uv pip install -e .
    pytest

``ruff check .`` and ``ruff format --check .`` gate CI,
along with ``scripts/vendor-check``.

The documentation you are reading builds with::

    uv run --group doc make -C doc html


Testing
-------

::

    pytest                      # unit tests: no root, no container, fast

    cmlxc deploy-cmrelay cmr0   # a real relay in an incus container
    cmlxc test-mini cmr0
    cmlxc test-cmdeploy cmr0

The unit suite runs without root and without a container:
it covers each row of the host classification table,
the activate planner against a unit-state snapshot,
the ownership rules, the ``www_folder`` cases,
signature verification, ``cmrelay dns``
against recorded ``drill`` output,
and release discovery with either source down.

The acceptance criterion is deliberately not a new test suite.
`cmlxc <https://github.com/chatmail/cmlxc>`_'s ``test-mini``
and ``test-cmdeploy`` already know how to exercise a deployed relay
over SMTP, IMAP and deltachat-rpc,
and they do not care how it was installed.
