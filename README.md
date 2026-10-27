# cmrelay

Install and maintain a [chatmail](https://chatmail.at) relay from one file.

`cmrelay-installer-<version>-<arch>-linux.run` unpacks a complete
relay stack into `/opt/cmrelay` and links the `cmrelay` command:
postfix, dovecot, filtermail, OpenDKIM, nginx, unbound,
chatmaild, acmetool, iroh-relay and chatmail-turn,
built as one Nix closure that carries its own libc and Python.
Nothing is installed from the distribution,
and there is no build machine and no ssh orchestration.
A working relay is two commands, run as root on the relay itself:

    ./cmrelay-installer-<version>-x86_64-linux.run
    cmrelay deploy chat.example.org

> [!WARNING] cmrelay is experimental:
> for throwaway containers and test hosts,
> not for migrating a production relay.
> `cmdeploy run` from [chatmail/relay](https://github.com/chatmail/relay)
> remains the supported path, and is also the way back.


## Commands

| | |
|---|---|
| `cmrelay deploy [DOMAIN]` | make this host match `chatmail.ini`, and report what moved |
| `cmrelay dns` | check the published DNS records against what this relay needs |
| `cmrelay status` | units, versions and conflicts; modifies nothing |
| `cmrelay upgrade [FILE]` | install a newer signed release; `--rollback` goes back |
| `cmrelay uninstall DOMAIN` | remove the stack, so `cmdeploy run` can take over |
| `cmrelay version` | cmrelay version, relay commit and component versions |

`DOMAIN` is needed only on a host with no `chatmail.ini` yet.
After that the domain is read from the ini and a domain given
on the command line is checked rather than obeyed,
because changing the mail domain strands every existing mailbox.

A deploy is idempotent:
it prints what changed and what was already right,
restarts only the services whose configuration moved, and a deploy
that changes nothing makes no network request and takes under a second.


## Configuration

`/opt/cmrelay/chatmail.ini` is the configuration, in the same format
and with the same settings as a `cmdeploy` relay.
It is created on the first deploy and never overwritten.
Edit it and run `cmrelay deploy` again.

The web pages are rendered from `/opt/cmrelay/www/`,
which is seeded from the release once and belongs to you from then on;
`/opt/cmrelay/www.dist/` keeps the release's own copy to diff against.
Edit `index.md` and run `cmrelay deploy`.


## Where things go

Everything cmrelay *defines* lives in `/opt/cmrelay`:
the ini, the rendered configuration for every component,
the unit files it indexes, and the software under `/opt/cmrelay/nix`,
a Nix store whose paths are rewritten to live there when the installer is built.
Everything holding live *state* stays where a `cmdeploy` relay keeps it:
mail and credentials under `/home/vmail/mail/<domain>`,
the DKIM key under `/etc/dkimkeys`, certificates
and the ACME account under `/var/lib/acme`.
Those are shared in place, never relocated and never copied,
which is what makes handing a host back to `cmdeploy run` cost nothing.


## Taking over a cmdeploy relay, and handing it back

On a host that already runs chatmail deployed by `cmdeploy run`,
`cmrelay deploy` recognises the situation, adopts the existing
`chatmail.ini`, stops and masks the distribution's conflicting units,
and asks before doing any of it. To hand the host back:

    cmrelay uninstall chat.example.org

This removes `/opt/cmrelay` and unmasks what the take-over masked;
`cmdeploy run` then restores the distribution's stack.


## Upgrading

    cmrelay upgrade

Without a file, GitHub is asked for the newest release.
Whatever the source, the candidate is verified against the release keys
shipped with the installed relay before it is unpacked, let alone run;
`--allow-unsigned` is the only way to install a locally built payload.
Each release becomes a new generation and the previous one stays complete
on disk, so `cmrelay upgrade --rollback` goes back in one step.


## Supported platforms

A Linux kernel, systemd, x86_64 or aarch64, a POSIX shell,
coreutils, root, and a writable `/opt`, `/etc` and `/usr/local`.
The host's libc and package manager are never involved,
and neither is a Nix it may run: nothing is installed under `/nix`. Out of scope:
Alpine and other musl or non-systemd distributions, and NixOS,
which generates its units and `/etc` itself. Immutable distributions are untested.


## Building and developing

    nix build '.?submodules=1#payload'   # the installer, in result/bin/
    uv venv && uv pip install -e . && uv pip install --group test
    pytest                               # no root, no container
    scripts/vendor-sync                  # after moving the relay submodule

`cmlxc deploy-cmrelay NAME` puts a build into a local
incus container, and `cmlxc test-mini NAME`
and `cmlxc test-cmdeploy NAME` are the acceptance suites.
The manual builds with `uv run --group doc make -C doc html`.
