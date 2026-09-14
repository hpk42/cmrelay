# What the payload ships, and under what terms

cmrelay's own licensing is a work in progress;
see [LICENSE](LICENSE). The installer it produces is
a Nix closure, and everything in it is somebody else's software
under somebody else's terms.
This file lists what a `cmrelay-installer-*.run` contains.

Where two licences are named with **or**, the component is offered under
either and the choice is yours. Where several are named with a comma, parts of
the component are under different terms and all of them apply.

The licence recorded here is the one nixpkgs records for the package cmrelay
builds, or the upstream project's own statement for the three pinned binaries.
`nix/closure.nix` names every component; `nix/fetch/binaries.nix` names the
three that are fetched rather than built.


## The relay stack

| component | licence |
|---|---|
| postfix | IPL-1.0 or EPL-2.0 |
| dovecot (the chatmail fork) | MIT, LGPL-2.1-only, BSD-3-Clause, BSD-4-Clause, public domain |
| nginx | BSD-2-Clause |
| unbound | BSD-3-Clause |
| OpenDKIM (the milter) | BSD-3-Clause |
| fcgiwrap | MIT |
| acmetool | MIT |
| ldns, for `drill` | BSD-3-Clause |

## The chatmail components

| component | licence |
|---|---|
| chatmaild, and the vendored configuration under `vendor/` | MIT |
| filtermail | source MIT; **the binary release cmrelay ships is GPL-3.0-or-later**, because it links viadkim |
| chatmail-turn | ISC-style permissive |
| iroh-relay | Apache-2.0 or MIT |

## The interpreter and its packages

| component | licence |
|---|---|
| CPython | Python-2.0 (PSF) |
| jinja2, markdown, qrcode | BSD-3-Clause |
| packaging | BSD-2-Clause or Apache-2.0 |
| crypt-r | Python-2.0 (PSF) |

## What everything above links against

| component | licence |
|---|---|
| glibc | LGPL-2.0-or-later |
| OpenSSL | Apache-2.0 |
| coreutils | GPL-3.0-or-later |
| iproute2, for `ss` | GPL-2.0-only |

The `.run` file additionally embeds two static binaries it needs before any of
the above can run: `zstd` (BSD-3-Clause) and a minimal `bsdtar` built from
libarchive (BSD-3-Clause).

Every one of these pulls in further dependencies, each under its own terms.
`nix build '.?submodules=1#closure'` and `nix path-info -r` on the result
enumerate them exactly for a given release.


## What is not shipped

`mtail` is not in the closure. A relay whose `chatmail.ini` sets
`mtail_address` expects the operator to install the binary at
`/usr/local/bin/mtail`, and `cmrelay deploy` refuses with the exact commands
to do so rather than fetching anything itself.
