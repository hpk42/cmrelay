"""What a chatmail relay needs on disk, written without pyinfra or apt."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined

from .. import paths
from ..system import systemd
from .chatmaild import chatmaild
from .dovecot import dovecot
from .filtermail import filtermail
from .iroh import iroh
from .journald import journald
from .mtail import mtail
from .nginx import nginx
from .opendkim import opendkim
from .postfix import postfix
from .tls import tls
from .turn import turn
from .unbound import unbound
from .website import website

# Configure order and start order; dovecot before postfix: it creates the auth socket.
COMPONENTS = [journald, unbound, turn, iroh, tls, website, chatmaild, opendkim]
COMPONENTS += [filtermail, dovecot, postfix, nginx, mtail]


class Deployment:
    """One deploy run against one host."""

    # A missing variable must fail loudly.
    JINJA = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    # These wait for the certificate; everything else starts first.
    TLS_UNITS = ("dovecot.service", "postfix.service", "nginx.service")

    def __init__(self, bundle, config, out, changes, disable_mail=False):
        self.bundle = bundle
        self.config = config
        self.out = out
        self.changes = changes
        self.disable_mail = disable_mail
        self.bin = bundle.bin
        self.source = bundle.package.joinpath

    def render(self, src, **data) -> str:
        return self.JINJA.from_string(self.source(src).read_text()).render(**data)

    def put_template(self, component, src, dest, owner=None, **data):
        self.changes.write(component, dest, self.render(src, **data), owner=owner)

    def put_file(self, component, src, dest, mode=0o644):
        self.changes.copy(component, self.source(src), dest, mode=mode)

    def put_unit(self, component, src, **data):
        name = Path(src).name.removesuffix(".j2")
        self.changes.install_unit(component, name, self.render(src, **data))

    def put_format_unit(self, component, src, **params):
        name = Path(src).name.removesuffix(".f")
        text = self.source(src).read_text().format(**params)
        self.changes.install_unit(component, name, text)

    def configure(self):
        """Write every file a chatmail relay needs, and no service state."""
        for component in COMPONENTS:
            component.configure(self)
        self.link_host_tools()
        commit = self.bundle.relay_commit
        self.changes.write("cmrelay", paths.VERSION_MARKER, f"{commit}\n")

    def link_host_tools(self):
        """Put the relay's tools on PATH; uninstall knows them by what they say or link to."""
        dovecot_args = ["-c", str(dovecot.CONF)]
        postfix_args = ["-c", str(postfix.CONF_DIR)]
        nginx_args = ["-p", self.bin("nginx", "prefix"), "-c", str(nginx.CONF)]
        wrapped = {
            "dovecot": (self.bin("dovecot"), dovecot_args),
            "doveadm": (self.bin("dovecot", "doveadm"), dovecot_args),
            "doveconf": (self.bin("dovecot", "doveconf"), dovecot_args),
            "postfix": (self.bin("postfix", "postfix"), postfix_args),
            "postconf": (self.bin("postfix", "postconf"), postfix_args),
            "postmap": (self.bin("postfix", "postmap"), postfix_args),
            "nginx": (self.bin("nginx"), nginx_args),
        }
        for name, (target, args) in wrapped.items():
            self.changes.write(
                "tools",
                paths.LOCAL_BIN / name,
                self.changes.marked(
                    f'#!/bin/sh\nexec {target} {" ".join(args)} "$@"\n'
                ),
                mode=0o755,
            )
        link = paths.LOCAL_BIN / "acmetool"
        # a binary cmdeploy installed on a taken-over host stays usable
        if not link.exists() or link.is_symlink():
            self.changes.symlink("tools", link, self.bin("acmetool"))

    def units(self) -> dict:
        """unit -> (on, restart) in start order; on None leaves a unit as it is.

        A unit restarts when this run wrote files of a component named in its row.
        """
        rows = [row for component in COMPONENTS for row in component.units(self)]
        changes = self.changes
        return {u: (on, any(n in changes for n in names)) for u, on, *names in rows}

    def verify(self) -> list:
        """What is missing: the DKIM key, TLS material, or a unit that should run."""
        problems = []
        expected = [name for name, (on, _) in self.units().items() if on]
        if not self.config.ipv4_relay:
            key = opendkim.KEYS / f"{opendkim.SELECTOR}.private"
            if not key.is_file():
                problems.append(f"no DKIM key at {key}")
        for path in (self.config.tls_cert_path, self.config.tls_key_path):
            if not Path(path).is_file():
                problems.append(f"no TLS material at {path}")
        for unit, state in systemd.unit_states(expected).items():
            if not state.is_up:
                problems.append(f"{unit} is not running ({state.active})")
        return problems
