.. _system-limits:

Configurable system limits
==========================

Limits for auto-rejecting address creation
------------------------------------------

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

The defaults suit a small machine:
a chatmail relay needs only 1GB RAM, one CPU,
and perhaps 10GB storage for a few thousand active addresses.

.. note::

   If you run a bigger machine,
   raise ``max_load_1m`` after watching ``uptime`` under typical load.

Rejections are logged by the ``doveauth`` service,
so you can check whether a limit is set too tightly::

    journalctl -u doveauth --grep 'registration rejected'

If ``mtail_address`` is set, rejections are also counted
in the ``rejected_registrations`` metric.


Overall IMAP and SMTP connection limits
---------------------------------------

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

To see how close a running relay is to these two limits,
copy :relayfile:`scripts/check-connections.sh` from the relay repository
onto the host and run it there::

    imap             5  ports 143,993   (max_imap_connections)
                     5  dovecot sessions, 0 of them in an active imap process
    submission       0  ports 465,587   (max_smtp_connections per port)
    incoming         0  port 25         (from other relays, no chatmail.ini limit)

It counts established sockets with ``ss``
and cross-checks the IMAP number against ``doveadm who``.
