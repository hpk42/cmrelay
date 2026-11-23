Where this manual comes from
============================

cmrelay installs the software of the
`chatmail relay repository <https://github.com/chatmail/relay>`_,
and several chapters of this manual are taken from that repository's documentation,
at commit |relay_commit|, which is also the commit the installed software comes from.
Both are under the MIT licence.

.. list-table::
   :header-rows: 1

   * - Chapter
     - Taken from
     - Changes
   * - :doc:`internals`
     - ``doc/source/overview.rst``
     - the sections on the relay itself, without the tour of the repository;
       the diagrams are pictures, the ports a table, three sentences are clarified,
       and DKIM is checked by filtermail, port 80 served by nginx
   * - :doc:`limits`
     - ``doc/source/getting_started.rst``
     - the "Configurable System Limits" section
   * - :doc:`proxy`
     - ``doc/source/proxy.rst``
     - a note that the rules forward no UDP for TURN
   * - :doc:`reverse_dns`
     - ``doc/source/reverse_dns.rst``
     - the opening sentence
   * - :doc:`iponly`
     - ``doc/source/iponly.rst``
     - ``cmrelay init`` instead of ``cmdeploy init``
   * - :doc:`faq`
     - ``doc/source/faq.rst``
     - the answers on upgrading and on how relays are run,
       and the question on cmrelay and cmdeploy

:doc:`getting_started`, :doc:`takeover`, :doc:`cmrelay` and :doc:`overview` are written for cmrelay.
