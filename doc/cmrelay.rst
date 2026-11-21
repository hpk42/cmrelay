The cmrelay command
===================

``cmrelay`` sets up and maintains a chatmail relay on the host it is executed on.
Every command runs as root.
This page shows the help of each command as ``cmrelay COMMAND --help`` prints it.

.. code-block:: console

   $ cmrelay --help
   usage: cmrelay [-h] COMMAND ...

   Install and maintain a chatmail relay.

   positional arguments:
     COMMAND
       init       write chatmail.ini for a domain
       deploy     make this host match its configuration
       dns        check the published records against what this host needs
       status     report units, versions and conflicts
       upgrade    install a newer signed release
       uninstall  hand the host back, so `cmdeploy run` can take over

   options:
     -h, --help   show this help message and exit

   The manual is at https://cmrelay.readthedocs.io/

cmrelay init
------------

.. code-block:: console

   $ cmrelay init --help
   usage: cmrelay init [-h] [DOMAIN]

   Write /opt/cmrelay/chatmail.ini for DOMAIN, a domain or an IPv4 address. On a
   cmdeploy relay its chatmail.ini is copied there and DOMAIN is optional.

   positional arguments:
     DOMAIN      the chatmail domain, or an IPv4 address

   options:
     -h, --help  show this help message and exit


cmrelay deploy
--------------

.. code-block:: console

   $ cmrelay deploy --help
   usage: cmrelay deploy [-h] [--disable-mail] [--skip-dns-check] [--verbose]
                         [--yes]

   Bring this host to what /opt/cmrelay/chatmail.ini says and report what
   changed. A second run changes nothing.

   options:
     -h, --help        show this help message and exit
     --disable-mail    configure everything but leave postfix and dovecot
                       stopped; the next deploy without it starts them
     --skip-dns-check  in acme mode, deploy even when the DNS records do not
                       point here
     --verbose, -v     name every file that changed
     --yes, -y         skip every prompt


cmrelay dns
-----------

.. code-block:: console

   $ cmrelay dns --help
   usage: cmrelay dns [-h] [--zonefile PATH]

   Query the authoritative nameservers for the records this host needs, and
   report what is missing. Exits 1 if a required record is absent.

   options:
     -h, --help       show this help message and exit
     --zonefile PATH  write the generated zone to PATH and exit


cmrelay status
--------------

.. code-block:: console

   $ cmrelay status --help
   usage: cmrelay status [-h] [--verbose]

   Report units, versions and conflicts, and an overall verdict. Modifies
   nothing. Exits 0 when healthy, 1 when degraded and 2 when nothing is
   configured.

   options:
     -h, --help     show this help message and exit
     --verbose, -v  list every package and unit, not only their number


cmrelay upgrade
---------------

.. code-block:: console

   $ cmrelay upgrade --help
   usage: cmrelay upgrade [-h] [--no-verify] [--force] [--skip-dns-check] [--yes]
                          FILE

   Install the signed release in FILE, an installer file, and deploy it. When
   that deploy fails, the installed release is deployed again, so all services
   stay on one release. To go back later, upgrade to the older installer file
   with --force.

   positional arguments:
     FILE              the installer file

   options:
     -h, --help        show this help message and exit
     --no-verify       skip the signature verification, for an installer you
                       built yourself
     --force           install a candidate that is not newer
     --skip-dns-check  passed on to the deploy that follows
     --yes, -y         skip the prompts


cmrelay uninstall
-----------------

.. code-block:: console

   $ cmrelay uninstall --help
   usage: cmrelay uninstall [-h] [--yes]

   Remove /opt/cmrelay, unmask what cmrelay masked and re-enable what it
   disabled. Asks first.

   options:
     -h, --help  show this help message and exit
     --yes, -y   do not ask

Exit status
-----------

0 on success, 1 on an error, 2 when no command is given, 130 when interrupted.
