Setting up a chatmail relay
===========================

``cmrelay`` installs a chatmail relay from a single file,
run as root on the host itself.

.. important::

   The host must NOT run a web or mail server.
   cmrelay brings its own nginx, postfix, dovecot and unbound,
   and they need the web and mail ports for themselves.
   The one exception is a relay deployed with ``cmdeploy run``,
   which cmrelay can take over and hand back, see :ref:`takeover`.

   cmrelay is experimental:
   use it for throwaway containers and test hosts, not for production.

You need root on a :ref:`supported Linux platform <platforms>`
with reachable SMTP, SUBMISSIONS, IMAPS, HTTP and HTTPS ports and UDP port 3478,
outgoing connections on port 25, and usually a domain whose DNS you control.


.. _setup:

Initial setup
-------------

The steps use ``chat.example.org``. Please substitute it with your own domain.

1. Create initial DNS records, presented here in BIND zone file format::

       chat.example.org. 3600 IN A 198.51.100.5
       chat.example.org. 3600 IN AAAA 2001:db8::5
       www.chat.example.org. 3600 IN CNAME chat.example.org.
       mta-sts.chat.example.org. 3600 IN CNAME chat.example.org.

   .. note::

      A domain starting with ``_`` (e.g. ``_chat.example.org``)
      gets self-signed certificates and needs no ``mta-sts`` records.

2. Download the latest ``cmrelay-installer-<version>-<arch>-linux.run`` file
   from https://github.com/hpk42/cmrelay/releases,
   where ``<arch>`` is what ``uname -m`` prints on the host, ``x86_64`` or ``aarch64``,
   and copy the file onto the host; you can :ref:`check its signature <verify>` first.

3. Run the installer as root::

       ./cmrelay-installer-<version>-<arch>-linux.run

   It unpacks into ``/opt/cmrelay`` and links the ``cmrelay`` command
   into ``/usr/local/bin``.

4. Initialize the chatmail configuration with your DNS domain name::

       cmrelay init chat.example.org

   This creates ``/opt/cmrelay/chatmail.ini``, containing ready-to-go default settings.

5. Configure and start the services.
   With ACME certificates it first checks the records from step 1 against this host,
   names the fix for one that is missing or points elsewhere,
   and offers to check again once it is published.
   For ``chat.example.org`` the first deploy prints:

   .. code-block:: console

      $ cmrelay deploy

      Relay
        Host        no chatmail relay on it yet
        Domain      chat.example.org  (acme, /home/vmail/mail/chat.example.org)
        Version     cmrelay 0.4.0.dev0, chatmail/relay 7a0bd2d4f8

      Configuration
          seeded /opt/cmrelay/www-src from the release's own pages
          generating a DKIM key for chat.example.org
        ~ acmetool          10 files
        ~ chatmail.ini      1 files
        ~ chatmaild         8 files
        ~ cmrelay           2 files
        ~ dovecot           5 files
        ~ fcgiwrap          2 files
        ~ filtermail        3 files
        ~ iroh              2 files
        ~ journald          1 files
        ~ nginx             12 files
        ~ opendkim          9 files
        ~ postfix           12 files
        ~ tools             8 files
        ~ turnserver        1 files
        ~ unbound           8 files
        ~ website           26 files

      Services
        ~ systemd-resolved.service (enabled/active) stopped and masked
          reconciling certificates for chat.example.org, mta-sts.chat.example.org, www.chat.example.org
        ~ enable: unbound.service, turnserver.service, iroh-relay.service,
          acmetool-redirector.service, acmetool-reconcile.timer, doveauth.service,
          chatmail-metadata.service, lastlogin.service, chatmail-expire.timer,
          chatmail-fsreport.timer, opendkim.service, filtermail.service,
          filtermail-incoming.service, filtermail-transport.service,
          fcgiwrap.socket, fcgiwrap.service, dovecot.service, postfix.service,
          nginx.service
        ~ start: unbound.service, turnserver.service, iroh-relay.service,
          acmetool-redirector.service, acmetool-reconcile.timer, doveauth.service,
          chatmail-metadata.service, lastlogin.service, chatmail-expire.timer,
          chatmail-fsreport.timer, opendkim.service, filtermail.service,
          filtermail-incoming.service, filtermail-transport.service,
          fcgiwrap.socket, fcgiwrap.service, dovecot.service, postfix.service,
          nginx.service
        ~ restart: systemd-journald.service

      Verify
          DKIM key present, TLS material present, services running

      deployed chat.example.org in 6.0s: 111 files written, 20 services started

      Next: `cmrelay dns` lists the DNS records still missing.

6. Check the DNS records::

       cmrelay dns

   This prints the records that are still missing, in zone file format.
   Please set the DNS records as needed, and run ``cmrelay dns`` afterwards to check again.


Congratulations!  Your relay should run fine now.
You can check system and relay status with ``cmrelay status``.
For ``chat.example.org`` it prints:

.. code-block:: console

   $ cmrelay status
   cmrelay          0.4.0.dev0
   config           /opt/cmrelay/chatmail.ini
   chatmail/relay   7a0bd2d4f8d078681eaab6edde387e9ce09d22fc
   closure          /opt/cmrelay/lib/store/xxxxxxxxxxxxxxxxxxxx-cmrelay-closure
   mail domain      chat.example.org
   tls mode         acme
   mailboxes        /home/vmail/mail/chat.example.org
   packages         13
   units            22, all good
   overall status   all green, congrats!

   use -v for more info


:doc:`cmrelay` lists every command and option.


Changing /opt/cmrelay/chatmail.ini
----------------------------------

``/opt/cmrelay/chatmail.ini`` is typically created by ``cmrelay init`` and never overwritten.
You can edit it anytime and re-run the deployment::

   cmrelay deploy

It only restarts the services whose configuration changed.
You will see a report of what changed (if anything).

``cmrelay deploy --disable-mail`` leaves postfix and dovecot stopped,
for example while DNS still points at another machine.


.. _upgrade:

Upgrading
---------

Cmrelay itself never downloads software.
You need to download a `newer installer file <https://github.com/hpk42/cmrelay/releases>`_ yourself and run::

   cmrelay upgrade NEWFILE

cmrelay will first verify the ``NEWFILE`` is signed by its own release key,
before unpacking and deploying the upgrade.


.. _verify:

Verifying the signature on cmrelay install files
------------------------------------------------

On upgrade, signatures on new installer files are automatically verified,
see previous section.

For the first install you may check the installer file
by storing the following script as ``/root/verify-cmrelay-signature``:

.. literalinclude:: ../installer/scripts/verify-cmrelay-signature
   :language: bash

You can then run it with an installer file as its argument::

   bash /root/verify-cmrelay-signature cmrelay-installer-<version>-<arch>-linux.run

It prints ``Signature Verified Successfully`` for an authentic installer,
and ``Signature Verification Failure`` or another error for any other file.


Modifying the home page
-----------------------

A deploy publishes a home page with a QR code for creating an address,
plus an info page and a privacy page.
The page source is ``/opt/cmrelay/www-src/`` and you can edit it there.
An upgrade never touches these pages.

Edit ``index.md`` and run ``cmrelay deploy`` to change your web page.

Notes on the ``www_folder`` setting in ``chatmail.ini``:

- ``www_folder = disabled`` disables publishing web pages.

- Any other value of ``www_folder`` is ignored but
  you may link ``/opt/cmrelay/www-src`` to your desired filesystem location.


Disable automatic address creation
----------------------------------

Address creation is denied while this file is present::

       touch /etc/chatmail-nocreate


.. _external-tls:

Running a relay with externally managed certificates
-----------------------------------------------------

If another tool (Traefik, certbot, ...) manages the certificate
on the host, set in ``chatmail.ini``::

    tls_external_cert_and_key = /path/to/fullchain.pem /path/to/privkey.pem

The deploy checks that both files exist and does not run ``acmetool``.
Renewal is yours.
A systemd path watcher reloads the services when the certificate file changes.
It uses inotify, which does not cross bind mounts;
there, trigger the reload after renewal::

      systemctl start tls-cert-reload.service


.. _takeover:

Taking over a cmdeploy relay, and handing it back
-------------------------------------------------

This section is for operators of a live relay deployed with ``cmdeploy run``.

On such a relay, ``cmrelay init`` copies the existing ``chatmail.ini``,
and ``cmrelay deploy`` offers a "take-over":
it stops and disables the distribution's units,
and masks those cmrelay has no replacement for.
**Mailboxes, the DKIM key and the certificates stay where they are and are not touched.**

To hand the host back::

   cmrelay uninstall

This removes ``/opt/cmrelay`` and the units cmrelay wrote,
unmasks and re-enables what it masked or disabled, starts nothing
apart from systemd-resolved where it was in use,
and gives ``/etc/resolv.conf`` back what the deploy replaced.
Use ``cmdeploy run`` to restore the original configuration.

``/opt/cmrelay`` includes ``chatmail.ini`` and ``www-src/``, so copy them first if you edited them.
Mailboxes, ``/etc/dkimkeys`` and ``/var/lib/acme`` stay as the deploy left them.


.. _platforms:

Supported tested platforms
--------------------------

Each ``cmrelay-installer-<version>-<arch>-linux.run`` installer runs on the respective supported platform:

================ ================= ==============================================
Distribution     arch              Tested in CI
================ ================= ==============================================
Debian 12        x86_64, aarch64   install, deploy, uninstall
Debian 13        x86_64, aarch64   install, deploy, uninstall
Ubuntu 24.04     x86_64, aarch64   install, deploy, uninstall
Ubuntu 26.04     x86_64, aarch64   install, deploy, uninstall
================ ================= ==============================================

The installer needs a writable ``/opt``, ``/etc`` and ``/usr/local``,
and brings its own libc and ``tar``.

Not tested: SELinux distributions (Fedora, RHEL).

Not supported: musl or non-systemd distributions (Alpine), and NixOS.

On both architectures CI also deploys two relays from the installer
into Debian 12 containers and runs the relay tests of
`cmlxc <https://github.com/chatmail/cmlxc>`_ against them with Delta Chat accounts:
once after the first deploy, and again after the second relay went through
an upgrade, a failing upgrade, an uninstall and a fresh deploy.

Across the two relays, the tests check that

- a message from one relay arrives on the other,
  also with only SMTP (ports 80 and 443 blocked) or only HTTPS (port 25 blocked) between them,
- two accounts become verified contacts through a QR code (securejoin),
- a message to a relay with ports 25, 80 and 443 blocked is deferred by the sending relay.

On each relay alone, the tests check that a message and a message of a single dot arrive,
that the first login creates the account and a login checks its domain,
that unencrypted messages are rejected,
and that a delivered message carries no trace of the sender's IP address.

The single-relay tests also run on a relay deployed with ``cmdeploy run``,
once cmrelay has taken it over, and again once ``cmdeploy run`` has taken it back.
