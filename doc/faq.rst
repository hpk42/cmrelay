

Frequently asked questions
===========================

What is the difference between chatmail relays and classic email servers?
--------------------------------------------------------------------------

A chatmail relay is a minimal Mail Transport Agent (MTA) setup that
goes beyond what classic email servers offer:

-  **Zero State:** no private data or metadata collected, messages are auto-deleted, low disk usage

-  **Instant/Realtime:** sub-second message delivery, realtime P2P
   streaming, privacy-preserving Push Notifications for Apple, Google, and `Ubuntu Touch <https://docs.ubports.com/en/latest/appdev/guides/pushnotifications.html>`_;

-  **Security Enforcement**: only strict TLS, DKIM and OpenPGP with minimized metadata accepted
   (DKIM is not enforced on :ref:`IP-only relays <iponly>`)

-  **Reliable Federation and Decentralization:** No spam or IP reputation checks, federating
   depends on established IETF standards and protocols.


How about interoperability with classic email servers?
-------------------------------------------------------

Generally, chatmail relays interoperate well with classic email servers.
However, some chatmail relays may be blocked by Big-Tech email
providers that use intransparent and proprietary techniques for scanning
and looking at cleartext email messages between users, or because they
use questionable IP-reputation systems that break interoperability.

**Chatmail relays instead use and require strong cryptography, allowing
anyone to participate, without having to submit to Big-Tech
restrictions.**

.. _selfhosted:

How are chatmail relays run? Can I run one myself?
--------------------------------------------------

Chatmail relays are designed to be very cheap to run, and are generally
self-funded by respective operators. Most chatmail relays are
deployed and updated using `the chatmail relay
repository <https://github.com/chatmail/relay>`__;
a cmrelay host runs the same software from a single installer file. Chatmail relays are
composed of proven standard email server components, Postfix and
Dovecot, and are configured to run unattended without much maintenance
effort. Chatmail relays happily run on low-end hardware like a Raspberry
Pi.

What is the difference between cmrelay and a cmdeploy relay?
------------------------------------------------------------

A cmdeploy relay is deployed from the `chatmail relay
repository <https://github.com/chatmail/relay>`__ with ``cmdeploy run``:
pyinfra connects from a second machine over ssh
and installs the distribution's packages.

cmrelay is derived from it and very compatible:
it installs the same software with the same ``chatmail.ini``,
ports and on-disk state, from a pinned commit of that repository.
What differs is the installing mechanism, which is self-contained:
one installer file, run on the host itself, brings every package along.
So cmrelay has fewer dependencies than a cmdeploy relay:
no second machine, no ssh access, no pyinfra and no distribution packages.

A relay can change from one to the other, see :ref:`takeover`.

How can I upgrade my chatmail relay?
------------------------------------

On a cmrelay host, run ``cmrelay upgrade FILE`` with a newer installer file; see :ref:`upgrade`.

How trustable are chatmail relays?
----------------------------------

Chatmail relays enforce end-to-end encryption,
and chatmail clients like `Delta Chat <https://delta.chat>`_
enforce end-to-end encryption on their own.

The end-to-end encryption protection includes attached media, user
display names, avatars and group names. What is visible to operators is:
message date, sender and receiver addresses.
Please see the `Delta Chat FAQ on encryption and security <https://delta.chat/en/help#e2ee>`_ for further info.
