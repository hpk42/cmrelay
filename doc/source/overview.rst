Technical overview
======================

This section describes the deployment architecture that cmrelay targets:
which components make up a running chatmail relay,
how they communicate, and where everything lives on the machine.
How the relay stack is assembled, released and developed
is a separate topic, described in :doc:`building`.


Deployed components
-------------------

``cmrelay deploy`` installs, configures and activates the components
of a chatmail relay, all running from ``/opt/cmrelay``
and wired together through systemd units:

-  Postfix_ is the Mail Transport Agent (MTA) and
   accepts messages from, and sends messages to, the wider email MTA network

-  Dovecot_ is the Mail Delivery Agent (MDA) and
   stores messages for users until they download them

-  `filtermail <https://github.com/chatmail/filtermail>`_
   prevents unencrypted email from leaving or entering the chatmail
   service and is integrated into Postfix’s outbound and inbound mail
   pipelines.

-  Nginx_ shows the web page with privacy policy and additional
   information, and hosts the CGI endpoint through which
   chat apps create new addresses (run by ``fcgiwrap``)

-  `acmetool <https://hlandau.github.io/acmetool/>`_ manages TLS
   certificates for Dovecot, Postfix, and Nginx

-  `OpenDKIM <http://www.opendkim.org/>`_ for signing messages with
   DKIM and rejecting inbound messages without DKIM

-  Unbound_ is the local caching DNS resolver
   which the relay's own lookups, DKIM verification included, go through

-  `mtail <https://google.github.io/mtail/>`_ for collecting anonymized
   metrics in case you have monitoring
   (the one binary cmrelay does not ship;
   the deploy preflight prints how to install it
   when ``mtail_address`` is set)

-  `Iroh relay <https://www.iroh.computer/docs/concepts/relay>`_ which
   helps client devices to establish Peer-to-Peer connections

-  `TURN <https://github.com/chatmail/chatmail-turn>`_ to enable relay
   users to start webRTC calls even if a p2p connection can’t be
   established

-  and the chatmaild services, explained in the next section.


chatmaild services
~~~~~~~~~~~~~~~~~~

:relaydir:`chatmaild <chatmaild>`
is a Python package containing several small services which handle
authentication, trigger push notifications on new messages, ensure
that outbound mails are encrypted, delete inactive users, and some
other minor things.

``chatmaild`` implements various systemd-controlled services
that integrate with Dovecot and Postfix to achieve instant-onboarding
and only relaying OpenPGP end-to-end encrypted messages. A
short overview of ``chatmaild`` services:

-  :relayfile:`doveauth <chatmaild/src/chatmaild/doveauth.py>`
   implements create-on-login address semantics and is used by Dovecot
   during IMAP login and by Postfix during SMTP/SUBMISSION login which
   in turn uses `Dovecot SASL
   <https://doc.dovecot.org/2.3/configuration_manual/authentication/dict/#complete-example-for-authenticating-via-a-unix-socket>`_
   to authenticate logins.

-  :relayfile:`chatmail-metadata <chatmaild/src/chatmaild/metadata.py>`
   is contacted by a
   :relayfile:`Dovecot lua script <cmdeploy/src/cmdeploy/dovecot/push_notification.lua>`
   to store user-specific relay-side config. On new messages, it
   :relayfile:`passes the user’s push notification token <chatmaild/src/chatmaild/notifier.py>`
   to
   `notifications.delta.chat <https://delta.chat/en/help#instant-delivery>`_
   so the push notifications on the user’s phone can be triggered by
   Apple/Google/Huawei.

-  :relayfile:`chatmail-expire <chatmaild/src/chatmaild/expire.py>`
   deletes old messages, large messages, and entire mailboxes
   of users who have not logged in for longer than
   ``delete_inactive_users_after`` days.

-  ``chatmail-quota-expire`` is called by Dovecot's ``quota_warning`` mechanism
   and will automatically remove oldest messages to keep mailboxes well under ``max_mailbox_size``.

-  :relayfile:`lastlogin <chatmaild/src/chatmaild/lastlogin.py>`
   is contacted by Dovecot when a user logs in and stores the date of
   the login.

chatmaild is not vendored into the cmrelay repository:
it is built from the pinned relay commit into the relay stack,
together with the Python interpreter every service above runs on.
See :doc:`building` for how that pinning works.


Where things go
---------------

Everything cmrelay *defines* lives in ``/opt/cmrelay``:
``chatmail.ini``, the rendered configuration for every component,
and the systemd unit files.
Everything holding live *state* stays where it already is::

    /opt/cmrelay/
    ├── chatmail.ini            # source of truth, written once, never overwritten
    ├── config/                 # rendered config, one directory per component
    ├── systemd/                # an index: one symlink per unit cmrelay owns
    ├── www/                    # your page source; seeded once, then yours
    ├── www.dist/               # the release's own pages, to diff against
    ├── generations/            # one per installed release, plus `current`
    ├── state/                  # what was masked, what was written, the lock
    └── nix/store/              # the relay stack itself

    /home/vmail/mail/<domain>   # mail and credentials
    /etc/dkimkeys/              # the DKIM signing key
    /var/lib/acme/              # certificates and the ACME account

Those last three are shared in place with whatever ran before,
never relocated and never copied.
That is what makes converting back to ``cmdeploy run`` cost nothing:
nothing was taken away, only which units are enabled changed.

Unit files are the exception to
"everything cmrelay defines lives in ``/opt/cmrelay``":
they are written into ``/etc/systemd/system``,
under exactly the unit names Debian uses
(``dovecot.service``, ``postfix.service`` and so on),
and indexed by symlink from ``/opt/cmrelay/systemd``.

``/nix`` is a symlink to ``/opt/cmrelay/nix``,
where the whole software stack lives as one Nix closure;
:doc:`building` explains why the store cannot live
under any other path.


What deploy owns, and what it does not
--------------------------------------

``/opt/cmrelay/config/**`` and the unit files are cmrelay's outright
and are regenerated every deploy. Everything else has a rule:

============================== ==================================================
path                           what happens
============================== ==================================================
``/etc/resolv.conf``           left alone once ``nameserver 127.0.0.1`` is first
``/etc/mailname``              content-compared write
``/etc/environment``           appended once, guarded
``/etc/aliases``               written only when absent
``/etc/systemd/journald.conf`` a guest file: a hand edit is reported, not overwritten
``/var/www/html``              a manifest; pages cmrelay no longer produces are removed, others left
``/usr/local/bin/*``           recorded, and removed only if cmrelay put them there
============================== ==================================================

cmrelay records the sha256 of every file it writes outside its own tree.
On the next deploy, a file matching neither the new render
nor the recorded hash was edited by hand:
for paths cmrelay owns it is overwritten, the previous content kept
beside it as ``<file>.replaced``, and the overwrite reported;
for guest files it is left alone and reported.

cmrelay never walks the mailbox tree.
The only reads permitted on the mailbox directory are a ``stat``
of the root and a capped directory-entry count,
because a production relay holds hundreds of thousands of maildirs.


Chatmail relay dependency diagram
---------------------------------

.. mermaid::
   :caption: This diagram shows relay components and dependencies/communication paths.

    graph LR;
        letsencrypt --- |80|acmetool-redirector;
        acmetool-redirector --- |443|nginx-right(["`nginx
        (external)`"]);
        nginx-external --- |465|postfix;
        nginx-external(["`nginx
        (external)`"]) --- |8443|nginx-internal["`nginx
        (internal)`"];
        nginx-internal --- website["`Website
        /var/www/html`"];
        nginx-internal --- newemail.py;
        nginx-internal --- autoconfig.xml;
        certs-nginx[("`TLS certs
        /var/lib/acme`")] --> nginx-internal;
        systemd-timer --- acmetool;
        systemd-timer --- chatmail-expire-daily;
        systemd-timer --- chatmail-fsreport-daily;
        acmetool --> certs[("`TLS certs
        /var/lib/acme`")];
        nginx-external --- |993|dovecot;
        postfix --- |SASL|dovecot;
        autoconfig.xml --- postfix;
        autoconfig.xml --- dovecot;
        postfix --- |10080|filtermail-outgoing;
        postfix --- |10081|filtermail-incoming;
        postfix --- |10083|filtermail-transport;
        filtermail-outgoing --- |10025 reinject|postfix;
        filtermail-incoming --- |10026 reinject|postfix;
        postfix --- |milter opendkim.sock|OpenDKIM
        dovecot --- |doveauth.socket|doveauth;
        dovecot --- |message delivery|maildir["maildir
        /home/vmail/.../user"];
        dovecot --- |lastlogin.socket|lastlogin;
        dovecot --- chatmail-metadata;
        dovecot --- |quota-warning|chatmail-quota-expire;
        chatmail-quota-expire --- maildir;
        lastlogin --- maildir;
        doveauth --- maildir;
        chatmail-expire-daily --- maildir;
        chatmail-fsreport-daily --- maildir;
        chatmail-metadata --- iroh-relay;
        chatmail-metadata --- |encrypted device token| notifications.delta.chat;
        certs-nginx --> postfix;
        certs-nginx --> dovecot;
        style certs fill:#ff6;
        style website fill:#ff6;
        style maildir fill:#ff6;
        style certs-nginx fill:#ff6;
        style nginx-external fill:#f66;
        style nginx-right fill:#f66;
        style postfix fill:#f66;
        style dovecot fill:#f66;
        style OpenDKIM fill:#f66;
        style notification-proxy fill:#f66;

Accepting and delivering mail
-----------------------------

.. mermaid::
    :caption: This diagram shows all the paths a message can take.

    flowchart LR
        subgraph chatmail relay
            subgraph postfix
                qmgr .-> lmtp-filtermail["lmtp/lmtp-filtermail (default_transport)"]
                qmgr .-> lmtp["lmtp (local_transport)"]
                lmtp --> cleanup["cleanup (lmtp_header_cleanup)"]
                bounce
                smtpd-submission["smtpd/submission"]
                smtpd-smtps["smtpd/smtps"]
                smtpd-reinject-outgoing["smtpd/reinject-outgoing"] --> authclean["cleanup/authclean (submission_header_cleanup)"]
                authclean --> qmgr
                smtpd-smtp["smtpd/smtp"]
                smtpd-reinject-incoming["smtpd/reinject-incoming"] --> qmgr
            end
            lmtp-filtermail --LMTP inet:10083--> filtermail-transport
            cleanup --LMTP unix:private/dovecot-lmtp --> dovecot
            dovecot --> maildir
            smtpd-submission --SMTP inet:10080--> filtermail-outgoing
            smtpd-smtps --SMTP inet:10080--> filtermail-outgoing
            filtermail-outgoing --SMTP inet:10025--> smtpd-reinject-outgoing
            open-dkim["OpenDKIM (signing only)"] <--milter unix:opendkim/opendkim.sock--> smtpd-reinject-outgoing
            bounce <--milter unix:opendkim/opendkim.sock--> open-dkim
            bounce --> qmgr
            nginx
            smtpd-smtp -.SMTP inet:10081.-> filtermail-incoming
            nginx -.HTTP inet:10082.-> filtermail-incoming
            filtermail-incoming --SMTP inet:10026--> smtpd-reinject-incoming
        end
        filtermail-transport -.SMTP inet:25.-> mta1[Remote relay]
        filtermail-transport -.HTTPS /mxdeliv.-> mta1
        client[Client] -.SMTP inet:587.-> smtpd-submission
        client -.SMTP inet:465.-> smtpd-smtps
        client -.SMTP inet:443.-> nginx
        nginx -.SMTP inet:465.-> smtpd-smtps
        mta2[Remote relay] -.SMTP inet:25.-> smtpd-smtp
        mta2 -.HTTPS /mxdeliv.-> nginx

Operational details of a chatmail relay
----------------------------------------

Mailbox directory layout
~~~~~~~~~~~~~~~~~~~~~~~~

Fresh chatmail addresses have a mailbox directory that contains:

-  a ``password`` file with the salted password required for
   authenticating whether a login may use the address to send/receive
   messages. If you modify the password file manually, you effectively
   block the user.

-  ``enforceE2EEincoming`` is a default-created file with each address.
   If present the file indicates that this chatmail address rejects
   incoming cleartext messages. If absent the address accepts incoming
   cleartext messages.

-  ``dovecot*``, ``cur``, ``new`` and ``tmp`` represent IMAP/mailbox
   state. If the address is only used by one device, the Maildir
   directories will typically be empty unless the user of that address
   hasn’t been online for a while.

App version information (experimental)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A chatmail relay ships the
:relayfile:`appversions.json <chatmaild/src/chatmaild/defaults/appversions.json>`
file of the ``chatmaild`` package
and serves its content under the IMAP METADATA key
``/shared/vendor/deltachat/appversions``.
Chat apps installed outside of app stores read this key
to learn about updates and where to download them.
The mechanism is experimental and may change.

On a cmrelay host the file lives inside the read-only relay stack,
so version information changes with ``cmrelay upgrade``:
each release serves the ``appversions.json``
of the relay commit it was built from.
There is no automatic refresh.

.. note::

   Note that as of August 2026, only Delta Chat Android Google Play version
   is beginning to support discovering app versions from relays.
   Generally, consumers of relay-provided app version information
   need to verify themselves that downloaded app files are valid.

Active ports
~~~~~~~~~~~~

Postfix_ listens on ports

- 25 (SMTP)

- 587 (SUBMISSION) and

- 465 (SUBMISSIONS)

Dovecot_ listens on ports

- 143 (IMAP) and

- 993 (IMAPS)

Nginx_ listens on port

- 8443 (HTTPS-ALT) and

- 443 (HTTPS) which multiplexes HTTPS, IMAP and SMTP using ALPN
  to redirect connections to ports 8443, 465 or 993.

`acmetool <https://hlandau.github.io/acmetool/>`_ listens on port:

- 80 (HTTP).

`chatmail-turn <https://github.com/chatmail/chatmail-turn>`_ listens on port

- 3478 UDP (STUN/TURN), and temporarily opens further UDP ports
  when users request them. UDP port range is not restricted, any free port
  may be allocated.

chatmail-core based apps will, however, discover all ports and
configurations automatically by reading the `autoconfig XML
file <https://www.ietf.org/archive/id/draft-bucksch-autoconfig-00.html>`_
from the chatmail relay server.

Email domain authentication (DKIM)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Chatmail relays enforce :rfc:`DKIM <6376>` to authenticate incoming emails
(except for :ref:`IP-only relays <iponly>`).
Incoming emails must have a valid DKIM signature with
Signing Domain Identifier (SDID, ``d=`` parameter in the DKIM-Signature
header) equal to the ``From:`` header domain. This property is checked
by OpenDKIM screen policy script before validating the signatures. This
corresponds to strict :rfc:`DMARC <7489>` alignment (``adkim=s``).
If there is no valid DKIM signature on the incoming email, the
sender receives a “5.7.1 No valid DKIM signature found” error.
After validating the DKIM signature,
the `final.lua` script strips all ``OpenDKIM:`` headers to reduce message size on disc.

Note that chatmail relays

- do **not** rely on DMARC and do not consult the sender policy published in DMARC records;

- do **not** rely on legacy authentication mechanisms such as
  :rfc:`iprev <8601#section-2.7.3>` and :rfc:`SPF <7208>`.
  Any IP address is accepted if the DKIM signature was valid.

Outgoing emails must be sent over authenticated connection with envelope
``MAIL FROM`` (return path) corresponding to the login.
This is ensured by Postfix which maps login username to ``MAIL FROM`` with
`smtpd_sender_login_maps <https://www.postfix.org/postconf.5.html#smtpd_sender_login_maps>`_
and rejects incorrectly authenticated emails with
`reject_sender_login_mismatch <https://www.postfix.org/postconf.5.html#reject_sender_login_mismatch>`_ policy.
``From:`` header must correspond to envelope ``MAIL FROM``, this is
ensured by ``filtermail`` proxy.

TLS requirements
~~~~~~~~~~~~~~~~

Filtermail (used for delivery) requires a valid TLS.

You can test it by resolving ``MX`` records of your relay domain and
then connecting to MX relays (e.g ``mx.example.org``) with
``openssl s_client -connect mx.example.org:25 -verify_hostname mx.example.org -verify_return_error -starttls smtp``
from the host that has open port 25 to verify that certificate is valid.

When providing a TLS certificate to your chatmail relay server, make
sure to provide the full certificate chain and not just the last
certificate.

If you use an external certificate manager (e.g. Traefik or certbot),
set ``tls_external_cert_and_key`` in ``chatmail.ini``
to provide the certificate and key paths.
See :ref:`external-tls` for details.

If you are running an Exim server and don’t see incoming connections
from a chatmail relay server in the logs, make sure ``smtp_no_mail`` log
item is enabled in the config with ``log_selector = +smtp_no_mail``. By
default Exim does not log sessions that are closed before sending the
``MAIL`` command. This happens if certificate is not recognized as valid
by Postfix, so you might think that connection is not established while
actually it is a problem with your TLS certificate.

If emails don’t arrive at your chatmail relay server, the
problem is likely that your relay does not have a valid TLS certificate.

Note that connections to relays with underscore-prefixed test domains
(e.g. ``_chat.example.org``) use ``encrypt`` tls security level,
because such domains cannot obtain valid Let's Encrypt certificates
and run with self-signed certificates.


.. _dovecot: https://dovecot.org
.. _postfix: https://www.postfix.org
.. _nginx: https://nginx.org
.. _unbound: https://unbound.docs.nlnetlabs.nl
