Setting up a chatmail relay
===========================

``cmrelay`` turns a Linux machine into a working chatmail relay:
postfix, dovecot, filtermail, OpenDKIM, nginx, unbound, chatmaild,
acmetool, iroh-relay and chatmail-turn, installed from a single file
and run as root on the relay itself.
There is no build machine and no ssh orchestration,
and nothing is installed from your distribution.
Addresses are created on first login,
so users onboard through chat apps and bots without any action from you.

.. warning::

   cmrelay is experimental: for throwaway containers and test hosts,
   not for migrating a production relay.
   The supported production path is ``cmdeploy run``,
   documented at https://chatmail.at/docs.

You need a machine on a :ref:`supported platform <platforms>`
with root access, reachable SMTP/SUBMISSIONS/IMAPS/HTTPS ports
and outgoing connections on port 25;
IPv6 is encouraged if available.
1GB RAM, one CPU and some 10GB of storage
carry a few thousand active addresses.
You also need control over a domain through a DNS provider of your choice
(there is experimental support for :ref:`IP-only relays <iponly>`).


.. _setup:

Setup with the single-file installer
------------------------------------

We use ``chat.example.org`` as the chatmail domain in the following
steps. Please substitute it with your own domain.

1. Setup the initial DNS records for your relay machine.
   The following is an example in the
   familiar BIND zone file format with a TTL of 1 hour (3600 seconds).
   Please substitute your domain and IP addresses.

   ::

       chat.example.org. 3600 IN A 198.51.100.5
       chat.example.org. 3600 IN AAAA 2001:db8::5
       www.chat.example.org. 3600 IN CNAME chat.example.org.
       mta-sts.chat.example.org. 3600 IN CNAME chat.example.org.

   .. note::

      For experimental deployments using self-signed certificates,
      use a domain name starting with ``_``
      (e.g. ``_chat.example.org``).
      The ``mta-sts`` CNAME and ``_mta-sts`` TXT records
      are not needed for such domains.

2. Download the latest ``cmrelay-installer-<version>-<arch>-linux.run``
   from https://github.com/chatmail/cmrelay/releases
   and copy it onto the relay machine.

3. On the relay machine, run the installer:

   ::

       ./cmrelay-installer-0.4.0-x86_64-linux.run

   The installer installs and nothing more:
   it unpacks the relay stack into ``/opt/cmrelay``
   and links the ``cmrelay`` command into ``/usr/local/bin``.
   It takes no arguments beyond ``-y``,
   which skips the prompt; it stages everything first
   and publishes it only after you have agreed to the plan it prints.

4. Configure and start the relay with your domain:

   ::

       cmrelay deploy chat.example.org

   This writes ``chatmail.ini``,
   renders the configuration for every component,
   registers the systemd units and starts the services.

   To use self-signed TLS certificates instead of Let's Encrypt,
   use a domain name starting with ``_``
   (e.g. ``cmrelay deploy _chat.example.org``).
   Domains starting with ``_`` cannot obtain WebPKI certificates,
   so self-signed mode is derived automatically.
   This is useful for private or test deployments.

5. Check the DNS records:

   ::

       cmrelay dns

   This queries the authoritative nameservers for the records
   this relay needs, and reports which are still missing,
   in a copy-paste-ready zone file format
   (it can take some time until new records are public).

From then on the installer is done and ``cmrelay`` is the tool.
Running the installer again on an installed machine modifies nothing
and points at ``cmrelay deploy`` and ``cmrelay upgrade`` instead.


Setup from PyPI
---------------

If you would rather not move a 50 MiB file around by hand:

::

    pip install cmrelay
    cmrelay install
    cmrelay deploy chat.example.org

``pip`` is the last step that needs the network.
The released wheels are per-architecture and carry the installer
payload inside them, so the relay install itself downloads nothing:
useful on a firewalled host.

``cmrelay install`` runs that bundled payload and is the wheel's
equivalent of running the ``.run`` file: it unpacks into
``/opt/cmrelay`` and stops there.
Every other command then runs from the installed relay,
so the steps are the same two as above.
A wheel never installs on its own:
a command typed before ``cmrelay install`` says so and stops.


Other helpful commands
----------------------

To check the status of your relay machine running the chatmail service,
its units, versions, conflicts and drift:

::

   cmrelay status

To display and check all recommended DNS records:

::

   cmrelay dns

To see what a deploy would change without changing anything:

::

   cmrelay deploy --dry-run

To print the cmrelay version, the relay commit it was built from,
component versions and the trusted release keys:

::

   cmrelay version

Changing configuration
----------------------

``/opt/cmrelay/chatmail.ini`` is the source of truth for the relay's
configuration. It is created on the first deploy and never overwritten.
Edit it and re-run:

::

   cmrelay deploy

A deploy is idempotent and fast: it reports what changed
and what was already right, and a no-op deploy takes under a second.
Single settings can also be overridden on the command line,
which updates the ini:

::

   cmrelay deploy --set max_load_1m=10

To start from an existing configuration file instead of generating one,
seed the first deploy with ``cmrelay deploy --config PATH``.

The domain is needed only on a machine that has no ``chatmail.ini`` yet.
After that it is read from the ini, and a domain given on the command
line is checked rather than obeyed:
changing the mail domain strands every existing mailbox,
so ``cmrelay deploy --force`` is reserved for exactly that override.


.. _upgrade:

Upgrading
---------

::

   cmrelay upgrade

Without a file, both GitHub and PyPI are asked for the newest release
and the highest version wins. Whichever answers, the candidate is
verified against the release keys shipped with the installed relay
before it is unpacked, let alone run.
You can also download an installer file yourself
and pass it explicitly as ``cmrelay upgrade FILE``.

``cmrelay upgrade --check`` says what would happen and stops.

Each release is installed as a new generation and the previous
generation stays complete and runnable on disk, so

::

   cmrelay upgrade --rollback

takes the relay back in one step.
:doc:`building` describes how generations and release signing work.

Installing and deploying never download software;
``cmrelay upgrade`` does, only when asked,
and names the URL before it does.


Modifying the home page
-----------------------

A deploy also creates default static web pages and serves them
through nginx:

-  a default ``index.html`` along with a QR code that users can click to
   create an address on your chatmail relay

-  a default ``info.html`` that is linked from the home page

-  a default ``privacy.html`` that is linked from the home page

There is no build machine, so the page source lives on the relay:
``/opt/cmrelay/www/`` is seeded from the release once and belongs
to you from then on.
All ``.html`` files are generated by the according markdown ``.md`` file
in that directory. Editing ``index.md`` and running

::

   cmrelay deploy --website-only

is the whole workflow.

``/opt/cmrelay/www.dist/`` always holds the installed release's
own version of those pages, so after an upgrade a deploy can name
which of yours have diverged and let you look. It never merges.


Custom web pages
----------------

You can skip publishing a web page by setting ``www_folder = disabled``
in ``chatmail.ini``; ``/var/www/html`` is then left alone.

If you want to manage your web pages elsewhere, you can set
``www_folder`` in ``chatmail.ini`` to a directory on the relay machine.
If it contains a ``src/index.md`` file, the cmdeploy source layout
is applied, so a tree copied over from a laptop works unchanged.


Disable automatic address creation
--------------------------------------------------------

If you need to stop address creation, e.g. because some script is wildly
creating addresses, run on the relay machine:

::

       touch /etc/chatmail-nocreate

Chatmail address creation will be denied while this file is present.


.. _system-limits:

Configurable System Limits
--------------------------

Limits for auto-rejecting address creation
..........................................

A relay refuses creation of new addresses
when the machine runs low on resources,
but existing addresses keep working.

Three ``chatmail.ini`` settings control this,
shown here with their defaults::

    max_load_1m = 5
    min_available_memory = 200M
    min_free_disk_space = 1G

- ``max_load_1m`` is the maximum 1-minute load average,
  as reported by ``uptime``;
  it counts processes waiting for disk I/O as well as for CPU.
  It is deliberately not scaled by the number of CPUs
  because I/O rather than CPU is what typically limits a relay.

- ``min_available_memory`` is the minimum memory available without swapping.

- ``min_free_disk_space`` is the minimum free disk space
  on the file system holding the mailboxes.

The defaults suit the small machine
described at the top of this page.

.. note::

   If you run a bigger machine,
   raise ``max_load_1m`` after watching ``uptime`` under typical load.

Rejections are logged by the ``doveauth`` service,
so you can check whether a limit is set too tightly::

    journalctl -u doveauth --grep 'registration rejected'

If ``mtail_address`` is set, rejections are also counted
in the ``rejected_registrations`` metric.
Note that cmrelay does not ship the ``mtail`` binary itself:
if ``mtail_address`` is set, the deploy preflight prints
how to install the pinned mtail release by hand.


Overall IMAP and SMTP connection limits
.......................................

Two further settings bound how many connections
the relay accepts at all, again shown with their defaults::

    max_imap_connections = 10000
    max_smtp_connections = 1000

``max_imap_connections`` becomes the Dovecot imap process limit,
and ``max_smtp_connections`` the Postfix process limit
on each of the submission and smtps ports.
A single client IP may use up to a fifth of ``max_smtp_connections``.
Each connection costs memory,
so these limits defend the relay against running out of RAM.

Unless ``imap_compress`` is enabled,
an IMAP connection that is idle for ``imap_hibernate_timeout``
is handed over to the ``imap-hibernate`` process
and does not count towards ``max_imap_connections``,
which is why a relay can serve far more IMAP clients
than this setting suggests.

If you run a large relay with 10k or 100k's of addresses,
check current connection counts before upgrading
and set the limits accordingly.

To see how close a running relay is to these two limits, copy
:relayfile:`check-connections.sh <scripts/check-connections.sh>`
from the relay repository onto the relay and run it there::

    imap             5  ports 143,993   (max_imap_connections)
                     5  dovecot sessions, 0 of them in an active imap process
    submission       0  ports 465,587   (max_smtp_connections per port)
    incoming         0  port 25         (from other relays, no chatmail.ini limit)

It counts established sockets with ``ss``
and cross-checks the IMAP number against ``doveadm who``.


Running a relay with self-signed certificates
----------------------------------------------

Use a domain name starting with ``_`` (e.g. ``_chat.example.org``)
to run a relay with self-signed certificates.
Domains starting with ``_`` cannot obtain WebPKI certificates
so the relay automatically uses self-signed certificates
and all other relays will accept connections from it
without requiring certificate verification.
This is useful for experimental setups and testing.

.. _external-tls:

Running a relay with externally managed certificates
-----------------------------------------------------

If you already have a TLS certificate manager
(e.g. Traefik, certbot, or another ACME client)
running on the relay machine,
you can configure the relay to use those certificates
instead of the built-in ``acmetool``.

Set the following in ``chatmail.ini``::

    tls_external_cert_and_key = /path/to/fullchain.pem /path/to/privkey.pem

The paths must point to certificate and key files on the relay machine,
managed by your external certificate tool.
During ``cmrelay deploy``, these paths are written into
the Postfix, Dovecot, and Nginx configurations,
and the deploy verifies that both files exist.
``acmetool`` is **not** run in this mode.

.. note::

   You are responsible for certificate renewal.
   When the certificate file changes on disk,
   all relay services pick up the new certificate automatically
   via a systemd path watcher installed during deploy.
   The watcher uses inotify, which does not cross bind-mount boundaries.
   If you use such a setup, you must trigger the reload explicitly after renewal::

      systemctl start tls-cert-reload.service


Taking over a cmdeploy-based relay, and handing back
----------------------------------------------------

cmrelay lands beside ``cmdeploy run``, not on top of it:
they cannot share a host, because both want
ports 25, 143, 465, 587 and 993.
On a host already running chatmail deployed by ``cmdeploy run``,
``cmrelay deploy`` recognises the situation and offers a take-over:

::

   cmrelay deploy --take-over

The host's existing ``chatmail.ini`` is adopted, and the mailboxes,
the DKIM key and the TLS certificates stay where they already are:
``/home/vmail/mail/<domain>``, ``/etc/dkimkeys`` and ``/var/lib/acme``
are shared in place, never relocated and never copied.
The distribution's own conflicting units are masked
so they cannot race the relay's.

To hand the host back:

::

   cmrelay uninstall chat.example.org

This removes ``/opt/cmrelay`` and ``/nix``
and unmasks what the take-over masked.
Nothing was taken away from the shared state directories,
so ``cmdeploy run`` can take over again at no cost.


.. _platforms:

Supported platforms
-------------------

cmrelay needs a Linux kernel, systemd, x86_64 or aarch64, a POSIX shell,
coreutils, root, and a writable ``/`` so ``/nix`` can be created.
The host's libc is irrelevant, because the relay stack carries its own,
and so is its ``tar``: the installer brings one.

The systemd units use nothing newer than systemd 229,
so no distribution still in support is below the floor:
the Debian family, the RHEL family, SUSE, Arch
and Gentoo built with systemd.

Out of scope for now:

- Alpine and the other musl or non-systemd distributions.

- Immutable and atomic distributions (Fedora CoreOS, Silverblue,
  openSUSE MicroOS, Ubuntu Core),
  because ``/nix`` cannot be created on a read-only root.

- NixOS, deliberately: it already has a store
  and its own declarative service model,
  so the right NixOS story is a module consuming the same derivations
  rather than this installer.
