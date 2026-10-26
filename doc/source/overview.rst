What is on a cmrelay host
=========================

How a chatmail relay works, which ports it uses
and how mail flows through it is documented
at https://chatmail.at/docs and is the same on a cmrelay host.
This page covers what is specific to a host installed by ``cmrelay``.


Components
----------

``cmrelay deploy`` configures and activates the components
of a chatmail relay, all running out of ``/opt/cmrelay``
and wired together through systemd units under the names Debian uses:

- Postfix_ (MTA), Dovecot_ (MDA),
  `filtermail <https://github.com/chatmail/filtermail>`_
  (the before-queue filter enforcing end-to-end encryption)

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

All of them are built or pinned from one commit
of the `chatmail relay repository <https://github.com/chatmail/relay>`_,
whose templates and units cmrelay uses unchanged;
``cmrelay version`` names that commit and every component version.

`mtail <https://google.github.io/mtail/>`_ is the one program cmrelay
does not ship. When ``mtail_address`` is set in ``chatmail.ini``,
the deploy refuses until ``/usr/local/bin/mtail`` exists,
and prints how to install the release relay pins.


Where things go
---------------

Everything cmrelay *defines* lives in ``/opt/cmrelay``;
everything holding live *state* stays where a ``cmdeploy`` relay keeps it::

    /opt/cmrelay/
    ├── chatmail.ini            # the configuration, written once, never overwritten
    ├── config/                 # rendered configuration, one directory per component
    ├── systemd/                # an index: one symlink per unit cmrelay owns
    ├── www/                    # your page source; seeded once, then yours
    ├── www.dist/               # the release's own pages, to diff against
    ├── generations/            # one per installed release, plus `current`
    ├── state/                  # what take-over masked, what is published
    └── nix/store/              # the relay stack itself

    /home/vmail/mail/<domain>   # mail and credentials
    /etc/dkimkeys/              # the DKIM signing key
    /var/lib/acme/              # certificates and the ACME account

Unit files are written into ``/etc/systemd/system`` under exactly
the names Debian uses (``dovecot.service``, ``postfix.service``
and so on) and indexed by symlink from ``/opt/cmrelay/systemd``.
``/nix`` is a symlink to ``/opt/cmrelay/nix``:
the software is a Nix closure whose binaries name ``/nix/store`` paths,
so the paths stay and the bytes live under ``/opt/cmrelay``.


What deploy owns, and what it does not
--------------------------------------

``/opt/cmrelay/config/**`` and the unit files are cmrelay's outright
and are regenerated every deploy. Outside its tree a deploy touches:

============================== ==================================================
path                           what happens
============================== ==================================================
``/etc/resolv.conf``           left alone once ``nameserver 127.0.0.1`` is first
``/etc/mailname``              content-compared write
``/etc/environment``           the ``TZ`` line is appended once
``/etc/aliases``               written only when absent
``/etc/systemd/journald.conf`` removed if an older deploy wrote it; relay's drop-in goes into ``journald.conf.d``
``/etc/chatmail-version``      the relay commit, as ``cmdeploy`` writes it
``/var/www/html``              pages cmrelay no longer produces are removed, others left
``/usr/local/bin/*``           tool links, recorded and removed only if cmrelay put them there
============================== ==================================================

Every deploy prints each file outside ``/opt/cmrelay`` it changed,
and collapses the ones inside into a count per component;
``--verbose`` names them all.

cmrelay never walks the mailbox tree. The only reads on the mailbox
directory are a ``stat`` of the root and a capped directory-entry count,
because a production relay holds hundreds of thousands of maildirs.


.. _dovecot: https://dovecot.org
.. _postfix: https://www.postfix.org
.. _nginx: https://nginx.org
.. _unbound: https://unbound.docs.nlnetlabs.nl
