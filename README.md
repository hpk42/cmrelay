# cmrelay repository quick intro

Install and maintain a [chatmail](https://chatmail.at) relay from one file.

`cmrelay-installer-<version>-<arch>-linux.run` unpacks the same software
a [cmdeploy relay](https://github.com/chatmail/relay) uses into `/opt/cmrelay`.
Instead of relying on (Debian 12) system packages
the software contained in a cmrelay installer is built with a "Nix closure" with its own libc and Python.
Run the installer as root directly on the host:

    ./cmrelay-installer-<version>-x86_64-linux.run


which will unpack the software contained in the installer into `/opt/cmrelay`.
To initialize and deploy your relay:

    cmrelay init chat.example.org

    cmrelay deploy

> [!WARNING] cmrelay is experimental:
> for throwaway containers and test hosts, not for production yet.
> `cmdeploy run` from [chatmail/relay](https://github.com/chatmail/relay)
> remains the supported path, and is also the way back.

## Supported platforms

Debian 12 or 13 and Ubuntu 24.04 or 26.04, x86_64 or aarch64, as root,
with a writable `/opt`, `/etc` and `/usr/local`.
The host must not run a web or mail server,
except a relay deployed with `cmdeploy run`, which cmrelay takes over.

## Where to read on

- [The manual](https://cmrelay.readthedocs.io/) covers setting up and operating a cmrelay host.
  Its source is in [doc/](doc/), and `make -C doc html` builds it into `build/doc/html` with [uv](https://docs.astral.sh/uv/),
  `make -C doc auto` serves it at http://127.0.0.1:8000/ and rebuilds on changes.

- [host/README.md](host/README.md) describes how the `cmrelay` tool is laid out and tested.

- [installer/README.md](installer/README.md) describes how the installer file is built,
  signed and released.

