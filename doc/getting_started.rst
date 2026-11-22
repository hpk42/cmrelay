Setting up a chatmail relay with cmrelay
========================================

``cmrelay`` installs a chatmail relay from a single file
that unpacks all pre-built packages the relay needs.

**Prerequisites:**

- a root shell, through ssh or a hoster's console,
  on a host with a :ref:`supported Linux platform <platforms>`,

- 1 GB of RAM and 10 GB of disk space,

- a DNS domain you control,

- the :ref:`mail and web ports <active-ports>` free and reachable,
  and outgoing connections to port 25.


.. _setup:

Initial setup
-------------

The install steps use ``chat.example.org``. Please substitute it with your own domain.

1. Create initial DNS records on your DNS hoster, presented here in BIND zone file format::

       chat.example.org. 3600 IN A 198.51.100.5
       chat.example.org. 3600 IN AAAA 2001:db8::5
       www.chat.example.org. 3600 IN CNAME chat.example.org.
       mta-sts.chat.example.org. 3600 IN CNAME chat.example.org.

2. On the host, download and run the installer for ``x86_64``::

       curl -fLO |release-url|/|installer-x86_64|
       sh |installer-x86_64|

   or ``aarch64``::

       curl -fLO |release-url|/|installer-aarch64|
       sh |installer-aarch64|

   The installer unpacks into ``/opt/cmrelay`` and links the ``cmrelay`` command
   into ``/usr/local/bin``, so you can usually directly execute it in the next step.

3. Initialize the chatmail configuration with your DNS domain name::

       cmrelay init chat.example.org

   This creates ``/opt/cmrelay/chatmail.ini``, containing ready-to-go default settings.

4. Deploy and start the services::

      cmrelay deploy

5. Check the DNS records::

       cmrelay dns

   This prints the records that are still missing, in zone file format.
   Please set the DNS records as needed, and run ``cmrelay dns`` afterwards to check again.

6. Check system status::

       cmrelay status


Congratulations!  Your relay should run fine now.

:doc:`cmrelay` lists every command and option.


Changing /opt/cmrelay/chatmail.ini
----------------------------------

``/opt/cmrelay/chatmail.ini`` is typically created by ``cmrelay init`` and never overwritten afterwards.
You can inspect its settings, edit it anytime and re-run the deployment::

   cmrelay deploy

It only restarts the services whose configuration changed.
You will see a report of what changed (if anything).

``cmrelay deploy --disable-mail`` leaves postfix and dovecot stopped,
for example while DNS still points at another machine.


.. _upgrade:

Upgrading
---------

Cmrelay itself never downloads software.
You need to download a new release from https://github.com/hpk42/cmrelay/releases
yourself and run ``cmrelay upgrade FILE`` with it, for the latest release on ``x86_64``::

   curl -fLO |release-url|/|installer-x86_64|
   cmrelay upgrade |installer-x86_64|

or on ``aarch64``::

   curl -fLO |release-url|/|installer-aarch64|
   cmrelay upgrade |installer-aarch64|

cmrelay will first verify the file is signed by its own release key,
before unpacking and deploying the upgrade.


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


.. _verify:

Verifying the signature on cmrelay install files
------------------------------------------------

On upgrade, signatures on new installer files are automatically verified,
see :ref:`upgrade`.

For the first install you may check the installer file
by storing the following script as ``/root/verify-cmrelay-signature``:

.. literalinclude:: ../installer/scripts/verify-cmrelay-signature
   :language: bash

You can then run it with an installer file as its argument, on ``x86_64``::

   bash /root/verify-cmrelay-signature |installer-x86_64|

or on ``aarch64``::

   bash /root/verify-cmrelay-signature |installer-aarch64|

It prints ``Signature Verified Successfully`` for an authentic installer,
and ``Signature Verification Failure`` or another error for any other file.


.. _platforms:

Supported tested platforms
--------------------------

Each ``cmrelay-installer-<version>-<arch>-linux.run`` installer runs on the respective supported platform:

================ ================= ==============================================
Distribution     arch              Verified in CI
================ ================= ==============================================
Debian 12        x86_64, aarch64   install, deploy, inter-relay tests, uninstall
Debian 13        x86_64, aarch64   install, deploy, uninstall
Ubuntu 24.04     x86_64, aarch64   install, deploy, uninstall
Ubuntu 26.04     x86_64, aarch64   install, deploy, uninstall
================ ================= ==============================================

The installer needs a writable ``/opt``, ``/etc`` and ``/usr/local``,
and brings its own libc and ``tar``.

Not tested: SELinux distributions (Fedora, RHEL).

Not supported: musl or non-systemd distributions (Alpine), and NixOS.


**Inter-relay tests**

On both architectures CI deploys two relays from the generated installer
into Debian 12 containers and runs the relay tests of
`cmlxc <https://github.com/chatmail/cmlxc>`_ against them with chat profiles:
once after the first deploy, and again after the second relay went through
an upgrade, a failing upgrade, an uninstall and a fresh deploy.
After the first deploy, CI also runs chatmail/relay's own
:relaydir:`tests <cmdeploy/src/cmdeploy/tests>` against the two relays.

Across the two relays, the tests check that

- a message from one relay arrives on the other,
  also with only SMTP (ports 80 and 443 blocked) or only HTTPS (port 25 blocked) between them,

- two profiles become encrypted contacts through a QR code (securejoin),

- a message to a relay with ports 25, 80 and 443 blocked is deferred by the sending relay.

On each relay alone, the tests check that a message and a message of a single dot arrive,
that a profile's first login creates its address and a login checks the address's domain,
that unencrypted messages are rejected,
and that a delivered message carries no trace of the sender's IP address.
