Setting up a chatmail relay
===========================

``cmrelay`` turns a Linux machine into a working chatmail relay:
postfix, dovecot, filtermail, OpenDKIM, nginx, unbound,
chatmaild, acmetool, iroh-relay and chatmail-turn, installed
from a single file and run as root on the relay itself.
There is no build machine and no ssh orchestration,
and nothing is installed from your distribution.
Addresses are created on first login, so users onboard
through chat apps and bots without any action from you.

.. warning::

   cmrelay is experimental:
   for throwaway containers and test hosts,
   not for migrating a production relay.
   The supported production path is ``cmdeploy run``,
   documented at https://chatmail.at/docs.

You need a machine on a :ref:`supported Linux platform <platforms>`
with root access, reachable SMTP, SUBMISSIONS, IMAPS and HTTPS ports
and outgoing connections on port 25; IPv6 is encouraged if available.
You also need control over a domain through a DNS provider of your choice.


.. _setup:

Setup
-----

We use ``chat.example.org`` as the chatmail domain in the following steps.
Please substitute it with your own domain.

1. Set up the initial DNS records for your relay machine,
   here in BIND zone file format with a TTL of one hour.
   Substitute your domain and IP addresses.

   ::

       chat.example.org. 3600 IN A 198.51.100.5
       chat.example.org. 3600 IN AAAA 2001:db8::5
       www.chat.example.org. 3600 IN CNAME chat.example.org.
       mta-sts.chat.example.org. 3600 IN CNAME chat.example.org.

   .. note::

      For experimental deployments using self-signed certificates,
      use a domain name starting with ``_`` (e.g. ``_chat.example.org``).
      The ``mta-sts`` CNAME and ``_mta-sts`` TXT
      records are not needed for such domains.

2. Download the latest ``cmrelay-installer-<version>-<arch>-linux.run``
   from https://github.com/chatmail/cmrelay/releases
   and copy it onto the relay machine.

3. On the relay machine, run the installer as root:

   ::

       ./cmrelay-installer-<version>-x86_64-linux.run

   It unpacks the relay stack into ``/opt/cmrelay``
   and links the ``cmrelay`` command into ``/usr/local/bin``,
   and does nothing else. It shows what it is about to write
   and asks first; ``-y`` skips the question.

4. Configure and start the relay with your domain:

   ::

       cmrelay deploy chat.example.org

   This writes ``chatmail.ini``, renders the configuration
   for every component, registers the systemd units and starts the services.
   A domain name starting with ``_`` cannot obtain a WebPKI certificate,
   so such a relay runs with self-signed certificates automatically,
   which is useful for private or test deployments.

5. Check the DNS records:

   ::

       cmrelay dns

   This queries the authoritative nameservers for the records this relay
   needs and reports which are still missing, in a copy-paste-ready zone
   file format (it can take some time until new records are public).

From then on the installer is done and ``cmrelay`` is the tool.
Running the installer again on an installed machine modifies nothing
and points at ``cmrelay deploy`` and ``cmrelay upgrade`` instead.


Other helpful commands
----------------------

To see the units, component versions and conflicts of the relay::

   cmrelay status

To print the cmrelay version, the relay commit it was built
from, component versions and the trusted release keys::

   cmrelay version


Changing configuration
----------------------

``/opt/cmrelay/chatmail.ini`` is the relay's configuration,
in the same format and with the same settings as a ``cmdeploy`` relay;
the settings are documented at https://chatmail.at/docs.
It is created on the first deploy
and never overwritten. Edit it and re-run::

   cmrelay deploy

A deploy is idempotent and fast:
it reports what changed and what was already right,
restarts only the services whose configuration moved, and a deploy
that changes nothing takes under a second and makes no network request.

To start from an existing configuration file instead of generating one,
seed the first deploy with ``cmrelay deploy --config PATH``.

The domain is needed only on a machine that has no ``chatmail.ini`` yet.
After that it is read from the ini, and a domain given
on the command line is checked rather than obeyed:
changing the mail domain strands every existing mailbox,
so it is only ever done by editing the ini.

``cmrelay deploy --disable-mail`` configures everything but leaves postfix
and dovecot stopped, for example while DNS still points at another machine.
The decision is remembered by every later deploy
until ``cmrelay deploy --enable-mail`` reverses it.


.. _upgrade:

Upgrading
---------

::

   cmrelay upgrade

Without a file, GitHub is asked for the newest release.
Whatever the source, the candidate is verified against the release keys
shipped with the installed relay before it is unpacked, let alone run.
You can also download an installer file yourself
and pass it explicitly as ``cmrelay upgrade FILE``;
``cmrelay upgrade --check`` says what would happen and stops.

Each release is installed as a new generation
and the previous generation stays complete and runnable on disk, so ::

   cmrelay upgrade --rollback

takes the relay back in one step.

Installing and deploying never download software;
``cmrelay upgrade`` does, only when asked, and names the URL before it does.


Modifying the home page
-----------------------

A deploy also creates default static
web pages and serves them through nginx:
a home page with a QR code that users can click to create an address
on your relay, plus an info page and a privacy page linked from it.

There is no build machine, so the page source lives on the relay:
``/opt/cmrelay/www/`` is seeded from the release once
and belongs to you from then on. All ``.html`` files are generated
from the markdown ``.md`` file of the same name in that directory.
Editing ``index.md`` and running ``cmrelay deploy`` is the whole workflow.

``/opt/cmrelay/www.dist/`` always holds the installed release's
own version of those pages, so after an upgrade a deploy can name
which of yours have diverged and let you look. It never merges.

You can skip publishing web pages by setting
``www_folder = disabled`` in ``chatmail.ini``;
``/var/www/html`` is then left alone. To keep the pages elsewhere
on the relay machine, set ``www_folder`` to that directory;
a tree with a ``src/index.md`` is rendered the way ``cmdeploy``
renders one, anything else is served as it stands.


Disable automatic address creation
----------------------------------

If you need to stop address creation, e.g. because some script
is wildly creating addresses, run on the relay machine::

       touch /etc/chatmail-nocreate

Chatmail address creation will be denied while this file is present.


Running a relay with externally managed certificates
-----------------------------------------------------

If you already have a TLS certificate manager (e.g.
Traefik, certbot, or another ACME client) running
on the relay machine, set in ``chatmail.ini``::

    tls_external_cert_and_key = /path/to/fullchain.pem /path/to/privkey.pem

The paths must point to certificate and key files on the relay machine,
managed by your external certificate tool. The deploy checks that both files
exist before writing anything, writes the paths into the Postfix,
Dovecot and Nginx configurations, and does not run ``acmetool``.

You are responsible for certificate renewal.
When the certificate file changes on disk, all relay services pick up
the new certificate via a systemd path watcher installed by the deploy.
The watcher uses inotify, which does not cross bind-mount boundaries;
if you use such a setup, trigger the reload explicitly after renewal::

      systemctl start tls-cert-reload.service


Taking over a cmdeploy relay, and handing it back
-------------------------------------------------

cmrelay and ``cmdeploy run`` cannot share a host,
because both want ports 25, 143, 465, 587 and 993.
On a host already running chatmail deployed by ``cmdeploy run``,
``cmrelay deploy`` recognises the situation and offers a take-over:
the host's existing ``chatmail.ini`` is adopted, and the mailboxes,
the DKIM key and the TLS certificates stay where they already are,
shared in place and never copied. The distribution's own conflicting
units are stopped and masked so they cannot race the relay's.

To hand the host back::

   cmrelay uninstall chat.example.org

This removes ``/opt/cmrelay``
and unmasks what the take-over masked, and starts nothing:
``cmdeploy run`` then restores the distribution's stack, at no cost
because nothing was taken away from the shared state directories.


.. _platforms:

Supported platforms
-------------------

cmrelay needs a Linux kernel, systemd, x86_64 or aarch64, a POSIX shell,
coreutils, root, and a writable ``/opt``, ``/etc`` and ``/usr/local``.
The host's libc is irrelevant, because the relay stack carries its own,
and so is its ``tar``: the installer brings one.
A Nix the host already runs is irrelevant too,
since nothing is installed under ``/nix``.

Out of scope:

- Alpine and the other musl or non-systemd distributions.

- NixOS, which generates its units and ``/etc`` itself;
  the right NixOS story is a module consuming
  the same derivations rather than this installer.

Immutable and atomic distributions are untested.
