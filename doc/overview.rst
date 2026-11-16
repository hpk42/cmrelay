What is on a cmrelay host
=========================

Cmrelay uses the same software, services, ports and setup as `cmdeploy relays <https://github.com/chatmail/relay>`_.
This page summarizes those and covers specifics for a ``cmrelay`` managed host.

Components
----------

``cmrelay deploy`` configures and activates the components
of a chatmail relay, all running out of ``/opt/cmrelay``
and wired together through systemd units under the names Debian uses:

- Postfix_ (MTA), Dovecot_ (MDA),
  `filtermail <https://github.com/chatmail/filtermail>`_

- Nginx_ for the web pages and the address-creation
  endpoint (run by ``fcgiwrap``),
  `acmetool <https://hlandau.github.io/acmetool/>`_ for TLS certificates

- `OpenDKIM <http://www.opendkim.org/>`_ and Unbound_, the local resolver

- `Iroh relay <https://www.iroh.computer/docs/concepts/relay>`_
  and `TURN <https://github.com/chatmail/chatmail-turn>`_
  for realtime connections

- the :relaydir:`chatmaild <chatmaild>`
  services:
  ``doveauth`` creating addresses on first login,
  ``chatmail-metadata`` for push notifications, ``lastlogin``,
  and the ``chatmail-expire`` and ``chatmail-fsreport`` timers

chatmaild comes from the pinned commit of the `chatmail/relay repository <https://github.com/chatmail/relay>`_,
which also names the filtermail, TURN and Iroh relay releases,
Dovecot from chatmail's own `Dovecot tree <https://github.com/chatmail/dovecot>`_,
acmetool from the release Debian 12 ships,
and Postfix, Nginx, fcgiwrap, OpenDKIM and Unbound from nixpkgs 26.05.

`mtail <https://google.github.io/mtail/>`_ is not shipped by cmrelay.
When ``mtail_address`` is set in ``chatmail.ini``,
the deploy refuses until ``/usr/local/bin/mtail`` exists,
and prints how to install it.


Where a live cmrelay holds its config and state
------------------------------------------------

All configuration lives in ``/opt/cmrelay``::

    /opt/cmrelay/
    ├── chatmail.ini            # the configuration, written once, never overwritten
    ├── www-src/                # web page source: seeded once, then yours to modify
    └── lib/                    # managed by cmrelay, not for editing
        ├── bin/cmrelay         # the command, linked from /usr/local/bin
        ├── cgi-bin/            # the script behind account creation
        ├── config/             # rendered configuration, one directory per component
        ├── state/              # what uninstall gives back, what is published, the store paths in use
        └── store/              # the dovecot/postfix/etc. software stack

    # live state, where a cmdeploy relay keeps it too
    /home/vmail/mail/<domain>   # mail and credentials
    /etc/dkimkeys/              # the DKIM signing key
    /var/lib/acme/              # certificates and the ACME account

**What else is touched outside /opt/cmrelay**

================================ ===============================================================
path                             what happens
================================ ===============================================================
``/etc/resolv.conf``             ``nameserver 127.0.0.1`` first; what it replaced is kept and ``uninstall`` restores it
``/etc/mailname``                set to the mail domain
``/etc/environment``             the timezone setting ``TZ=:/etc/localtime`` is appended once
``/etc/systemd/system``          unit files and drop-ins; on netplan hosts one that runs wait-online without ``--dns``
``/etc/systemd/journald.conf.d`` a drop-in that keeps the journal in memory, three days of it
``/etc/sysusers.d``              ``cmrelay.conf``, the accounts the services run as
``/etc/sysctl.d``                ``60-cmrelay-inotify.conf``, the inotify limits dovecot needs
``/etc/acme/hooks``              the hook that reloads nginx after a renewal
``/etc/opendkim``                created empty
``/etc/unbound/unbound.conf.d``  created if absent; unbound includes the ``*.conf`` in it
``/etc/chatmail-version``        the chatmail/relay commit the software comes from
``/var/lib``, ``/var/log``       state and log directories of unbound, nginx and postfix
``/var/spool/postfix``           the postfix queue
``/var/www/html``                rendered webpages served by nginx
``/usr/local/bin/*``             wrapper scripts, and links into /opt/cmrelay
================================ ===============================================================

Every file cmrelay writes in ``/etc/systemd``, ``/etc/acme/hooks``, ``/etc/sysusers.d``,
``/etc/sysctl.d`` and ``/usr/local/bin`` starts with ``# written by cmrelay VERSION``,
after the ``#!`` line of a script. ``uninstall`` removes exactly these files and the links,
and leaves everything else in those directories alone.

.. _dovecot: https://dovecot.org
.. _postfix: https://www.postfix.org
.. _nginx: https://nginx.org
.. _unbound: https://unbound.docs.nlnetlabs.nl
