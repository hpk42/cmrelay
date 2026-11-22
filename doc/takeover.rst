.. _takeover:

Moving a cmdeploy relay to cmrelay and back
===========================================

This page is for operators of a relay deployed from `chatmail/relay <https://github.com/chatmail/relay>`_
who currently use ``cmdeploy run`` invocations to update their relay host.
The cmrelay installer, and the ``cmrelay`` command it offers, **run on the host itself**.
A ``cmrelay`` managed host uses the same software as cmdeploy,
all contained in the single installer file of each ``cmrelay`` version
which lets cmrelay take over cmdeploy relays seamlessly and hand them back if needed.


Taking over a cmdeploy relay
----------------------------

cmrelay automatically detects a cmdeploy relay and offers to take it over,
re-using its settings, TLS certificates and DKIM key, as well as the user mailboxes.

As ``root`` on the cmdeploy relay host:

1. Download the cmrelay installer for ``x86_64`` and run it::

       curl -fLO |release-url|/|installer-x86_64|
       sh |installer-x86_64|

   or download and run for ``aarch64``::

       curl -fLO |release-url|/|installer-aarch64|
       sh |installer-aarch64|

   This unpacks everything into ``/opt/cmrelay`` and links the ``cmrelay`` command into ``/usr/local/bin``.
   The cmdeploy relay keeps running as before until step 3.

2. Adopt the cmdeploy relay's configuration::

       cmrelay init

   This copies ``/usr/local/lib/chatmaild/chatmail.ini`` to ``/opt/cmrelay/chatmail.ini``.
   It needs no domain, and refuses one other than the relay's because
   a new domain would strand the existing addresses and invalidate the published DKIM record.

3. Take over the cmdeploy relay::

       cmrelay deploy

   It shows the chatmail/relay commit the relay ran and the one this cmrelay carries,
   lists the units a take-over stops and disables, and asks before it converts the host.
   It stops and disables postfix, dovecot, nginx, unbound, opendkim and fcgiwrap,
   and masks exim4, mta-sts-daemon and postfix's default instance,
   which cmrelay has no replacement for.
   **Mailboxes, the DKIM key and the certificates stay where they are and are not touched.**


Handing it back
---------------

In case there are problems with the operations of your relay,
you can uninstall cmrelay.
``/opt/cmrelay`` includes ``chatmail.ini`` and ``www-src/``,
so copy them first if you edited them.
Then::

   cmrelay uninstall

This recursively removes ``/opt/cmrelay`` and the units cmrelay wrote,
unmasks and re-enables what it masked or disabled,
and starts nothing but the relay's unbound, which ``/etc/resolv.conf`` names,
so that the host keeps resolving names until ``cmdeploy run``.
Mailboxes, ``/etc/dkimkeys`` and ``/var/lib/acme`` stay as the deploy left them.
Use ``cmdeploy run`` to restore the original configuration.


Tested in CI
------------

CI runs the checks on a single relay, listed under :ref:`platforms`,
on a relay deployed with ``cmdeploy run``,
once cmrelay has taken it over, and again once ``cmdeploy run`` has taken it back.
