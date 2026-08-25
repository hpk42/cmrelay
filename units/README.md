# The units cmrelay authors

Twenty-one of the units a chatmail relay runs are vendored verbatim from
relay, because relay already ships them and they are already parameterised on
the binary path. These six are the exception: they run the daemons Debian
supplies units for and nixpkgs does not, since NixOS generates units from
modules instead of shipping them in packages.

The NixOS service modules under `nixos/modules/services/` are the reference
for the `ExecStart` lines and ordering here; they are MIT licensed.

Two constraints apply to every file in this directory.

**Unit names are a contract.** `dovecot.service`, `postfix.service` and the
rest keep exactly the names Debian uses, because relay's own code and the
acceptance suite refer to them, and `journalctl -u dovecot` is how the tests
read logs. This is also why the preflight exists: identical names are what
makes a distribution's own packages collide.

**Directives stay above the systemd 229 floor**, so no distribution still in
support is below it.

Every store path is a Python `str.format` field, filled in at install time by
`Deployment.daemon_unit_vars()`.
