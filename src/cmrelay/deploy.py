"""The effects of relay's deployers, reproduced without pyinfra or apt.

The pyinfra deployer modules are reimplemented rather than vendored,
because they are the part that would rot. `configure()` follows
cmdeploy/src/cmdeploy/deployers.py's all_deployers order so the two can be
read side by side, and the comments name the deployer each block mirrors.
The deployers relay keeps in modules of their own live in
`cmrelay.daemons`; unit planning and activation in `cmrelay.activation`.

What does not carry over: apt.packages(), because the component is already in
the closure; Debian mechanics like policy-rc.d and the dovecot apt pin, which
mean nothing without apt; and binary paths, which are store paths here and
already template variables in every unit.

Every mutation goes through `self.j`, the run's journal. `configure()` writes
files and nothing else; `plan()` computes the intended unit state without
issuing a systemctl call.
"""

import subprocess
from pathlib import Path

from . import daemons, installer, paths, render, users, www
from .daemons import DeployError
from .version import __version__


def first_nameserver(resolv: Path = None) -> str:
    resolv = paths.RESOLV_CONF if resolv is None else resolv
    if not resolv.is_file():
        return ""
    for line in resolv.read_text(errors="replace").splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            return fields[1]
    return ""


# Matches ChatmailVenvDeployer.units; the timers own expire and fsreport.
CHATMAILD_UNITS = [
    "doveauth",
    "chatmail-metadata",
    "lastlogin",
    "chatmail-expire",
    "chatmail-expire.timer",
    "chatmail-fsreport",
    "chatmail-fsreport.timer",
]
TIMER_DRIVEN = {"chatmail-expire", "chatmail-fsreport"}


class Deployment:
    """One deploy run against one host."""

    def __init__(
        self,
        bundle,
        config,
        out,
        journal,
        disable_mail=False,
    ):
        self.bundle = bundle
        self.config = config
        self.out = out
        self.j = journal
        self.disable_mail = disable_mail
        self.spool_created = False

    @property
    def mail_domain(self) -> str:
        return self.config.mail_domain

    def bin(self, component, name=None) -> str:
        return self.bundle.bin(component, name)

    def vendor(self, relpath) -> Path:
        return self.bundle.vendor_file(relpath)

    def template_vars(self) -> dict:
        """The values the patched templates need beyond relay's own Config.

        Every entry corresponds to a hunk in patches/.
        """
        return {
            "postfix_config_dir": str(paths.config_dir("postfix")),
            "dovecot_config_dir": str(paths.config_dir("dovecot")),
            "nginx_prefix": self.bin("nginx", "prefix"),
            "cgi_bin_dir": str(paths.CGI_BIN),
            "quota_expire_bin": self.bin("python", "chatmail-quota-expire"),
            "ssl_dh_path": self.bin("dovecot", "dh_params"),
            "tls_ca_path": self.ca_path(),
            "www_root": str(paths.WWW_ROOT),
        }

    @staticmethod
    def ca_path() -> str:
        """The host's CA bundle; a store copy would freeze it at build time."""
        for candidate in ("/etc/ssl/certs", "/etc/pki/tls/certs"):
            if Path(candidate).is_dir():
                return candidate
        return "/etc/ssl/certs"

    def put_template(self, component, src, dest, **data):
        content = render.render_template(self.vendor(src), **data)
        return self.j.write(component, Path(dest), content)

    def put_file(self, component, src, dest, mode=0o644, owner=None):
        return self.j.copy(
            component, self.vendor(src), Path(dest), mode=mode, owner=owner
        )

    def unit_from_template(self, component, src, name=None, **data):
        content = render.render_template(self.vendor(src), **data)
        name = name or Path(src).name.removesuffix(".j2")
        return self.j.install_unit(component, name, content)

    def unit_verbatim(self, component, src, name=None):
        name = name or Path(src).name
        return self.j.install_unit(component, name, self.vendor(src).read_text())

    def shell(self, *argv, check=True, **kwargs):
        result = subprocess.run(
            [str(a) for a in argv],
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
        if check and result.returncode:
            raise DeployError(
                f"{' '.join(str(a) for a in argv)} failed"
                f" ({result.returncode}):\n{result.stderr.strip()}"
            )
        return result

    def configure(self):
        """Write every file a chatmail relay needs, and no service state."""
        self.configure_base()
        self.remove_legacy()
        daemons.configure_filtermail(self)
        self.configure_journald()
        self.configure_unbound()
        self.configure_turn()
        self.configure_iroh()
        self.configure_tls()
        self.configure_website()
        self.configure_chatmaild()
        self.configure_mtasts()
        if not self.config.ipv4_relay:
            daemons.configure_opendkim(self)
        # Dovecot before postfix: it creates the authentication socket
        # postfix needs.
        daemons.configure_dovecot(self)
        daemons.configure_postfix(self)
        self.configure_fcgiwrap()
        daemons.configure_nginx(self)
        daemons.configure_mtail(self)
        self.link_host_tools()
        self.configure_version_marker()

    # ChatmailDeployer

    def configure_base(self):
        # metadata crashes if the mailboxes dir does not exist
        self.j.ensure_directory(
            "chatmaild",
            Path(self.config.mailboxes_dir),
            mode=0o700,
            owner=("vmail", "vmail"),
        )
        users.verify_mailbox_ownership(self.config.mailboxes_dir)

        # Used by the auth proxy. https://wiki.debian.org/EtcMailName
        self.j.write("postfix", paths.MAILNAME, f"{self.mail_domain}\n")

    # LegacyRemoveDeployer

    def remove_legacy(self):
        for path in [*paths.LEGACY, paths.WWW_ROOT / "metrics"]:
            self.j.remove_file("legacy", path)
        for path in paths.LEGACY_DIRS:
            self.j.remove_directory("legacy", path)

    # JournaldDeployer

    def configure_journald(self):
        self.j.copy(
            "journald", self.vendor("config/journald.conf"), paths.JOURNALD_CONF
        )

    # UnboundDeployer

    def configure_unbound(self):
        self.write_unbound_anchor_seed()
        self.ensure_resolver_points_here()

        conf_dir = paths.config_dir("unbound")
        drop_in_dir = conf_dir / "unbound.conf.d"
        self.j.ensure_directory("unbound", drop_in_dir)
        self.put_template(
            "unbound",
            "config/unbound/unbound.conf.j2",
            drop_in_dir / "chatmail.conf",
            disable_ipv6=self.config.disable_ipv6,
        )
        self.write_unbound_base(conf_dir, drop_in_dir)

    def ensure_resolver_points_here(self):
        """Make the host resolve through its own unbound, and nothing more.

        Rewriting resolv.conf outright would delete an operator's fallback
        resolver or search domain. Once 127.0.0.1 is first, leave it alone.
        """
        if first_nameserver() == "127.0.0.1":
            return
        # trailing newline: a SolusVM bug needs it
        self.j.write(
            "unbound", paths.RESOLV_CONF, "nameserver 127.0.0.1\nnameserver 9.9.9.9\n"
        )

    def write_unbound_base(self, conf_dir: Path, drop_in_dir: Path):
        """The top-level unbound.conf, which relay never supplies.

        relay writes only a drop-in; Debian's package ships the rest and a
        store unbound has none of it. /etc/unbound/unbound.conf.d is included
        because other tooling drops files there.
        """
        self.j.ensure_directory(
            "unbound", paths.UNBOUND_STATE, owner=("unbound", "unbound")
        )
        self.j.ensure_directory("unbound", paths.UNBOUND_DROPINS)

        # Two anchors for one zone is fatal, and Debian's unbound ships one.
        # Yield rather than remove: it is a conffile `cmdeploy run` must find.
        anchor_line = f'    auto-trust-anchor-file: "{paths.UNBOUND_ANCHOR}"\n'
        if self.anchor_already_configured(paths.UNBOUND_DROPINS):
            self.out.detail(
                f"{paths.UNBOUND_DROPINS} already sets the trust anchor;"
                " leaving it to do so"
            )
            anchor_line = ""

        self.j.write(
            "unbound",
            conf_dir / "unbound.conf",
            "# written by cmrelay; regenerated on every run\n"
            "server:\n"
            '    username: "unbound"\n'
            '    directory: "/var/lib/unbound"\n'
            '    chroot: ""\n'
            '    pidfile: "/run/unbound.pid"\n' + anchor_line + "\n"
            f'include: "{drop_in_dir}/*.conf"\n'
            f'include: "{paths.UNBOUND_DROPINS}/*.conf"\n',
        )

    @staticmethod
    def anchor_already_configured(drop_ins=None) -> bool:
        """Whether something in the distribution's drop-ins anchors a zone."""
        drop_ins = Path(drop_ins) if drop_ins else paths.UNBOUND_DROPINS
        if not drop_ins.is_dir():
            return False
        for conf in sorted(drop_ins.glob("*.conf")):
            for line in conf.read_text(errors="replace").splitlines():
                bare = line.split("#", 1)[0].strip()
                if bare.startswith(("auto-trust-anchor-file:", "trust-anchor-file:")):
                    return True
        return False

    def write_unbound_anchor_seed(self):
        """Seed unbound's root trust anchor, but only when there is none.

        From then on the file is unbound's own, per RFC 5011.
        """
        anchor = paths.UNBOUND_ANCHOR
        self.j.write(
            "unbound",
            paths.UNBOUND_ANCHOR_SEED,
            "#!/bin/sh\n"
            "# written by cmrelay; regenerated on every run\n"
            f"test -s {anchor} && exit 0\n"
            f"exec {self.bin('unbound', 'unbound-anchor')} -a {anchor}\n",
            mode=0o755,
        )

    # TurnDeployer

    def configure_turn(self):
        content = render.render_format(
            self.vendor("systemd/turnserver.service.f"),
            bin_path=self.bin("chatmail-turn"),
            mail_domain=self.config.mail_domain_bare,
            execpath=self.bin("chatmail-turn"),
            config_path=str(paths.INI),
        )
        self.j.install_unit("turnserver", "turnserver.service", content)

    # IrohDeployer

    def configure_iroh(self):
        config_path = paths.config_dir("iroh") / "iroh-relay.toml"
        self.put_file("iroh", "config/iroh-relay.toml", config_path)
        self.unit_from_template(
            "iroh",
            "systemd/iroh-relay.service.j2",
            bin_path=self.bin("iroh-relay"),
            config_path=str(config_path),
        )

    # the TLS deployers

    def configure_tls(self):
        mode = self.config.tls_cert_mode
        if mode == "acme":
            daemons.configure_acmetool(self)
        elif mode == "self":
            daemons.configure_selfsigned(self)
        elif mode == "external":
            daemons.configure_external_tls(self)
        else:
            raise DeployError(f"Unknown tls_cert_mode: {mode}")

    def tls_domains(self) -> list:
        bare = self.config.mail_domain_bare
        return [bare, f"mta-sts.{bare}", f"www.{bare}"]

    # WebsiteDeployer

    def configure_website(self):
        www.seed(self.j, self.bundle, self.out)
        www.report_divergence(self.out, __version__)
        source = www.resolve(self.config)
        if source.warn:
            self.out.warn(source.warn)
        try:
            www.publish(self.j, self.config, source, self.out)
        except Exception as exc:  # noqa: BLE001 - a broken page must not fail a deploy
            self.out.warn(f"web page build failed, skipping website: {exc}")

    # ChatmailVenvDeployer

    def configure_chatmaild(self):
        for unit in CHATMAILD_UNITS:
            basename = unit if "." in unit else f"{unit}.service"
            script = unit.split(".")[0]
            content = render.render_format(
                self.vendor(f"systemd/{basename}.f"),
                execpath=self.bin("python", script),
                config_path=str(paths.INI),
                remote_venv_dir=self.bin("python", "path"),
                mail_domain=self.config.mail_domain_bare,
            )
            self.j.install_unit("chatmaild", basename, content)

    # MtastsDeployer

    def configure_mtasts(self):
        # postfix-mta-sts-resolver is dead. This is a pure removal step.
        self.j.remove_file("mta-sts", paths.unit_file("mta-sts-daemon.service"))

    # FcgiwrapDeployer

    def configure_fcgiwrap(self):
        """Both units: the service is socket-activated."""
        for name in ("fcgiwrap.socket", "fcgiwrap.service"):
            content = (
                (self.bundle.units / name)
                .read_text()
                .format(fcgiwrap_bin=self.bin("fcgiwrap"))
            )
            self.j.install_unit("fcgiwrap", name, content)

    def find_chatmaild_resource(self, name: str) -> Path:
        result = self.shell(
            self.bin("python"),
            "-c",
            "import importlib.resources,sys;"
            f"print(importlib.resources.files('chatmaild').joinpath({name!r}))",
        )
        path = Path(result.stdout.strip())
        if not path.is_file():
            raise DeployError(f"chatmaild does not ship {name}")
        return path

    # tools on the operator's PATH

    def link_host_tools(self):
        """Put the tools relay invokes by bare name where PATH will find them.

        dovecot's and postfix's get a wrapper, because cmrelay's
        configuration is not where they were compiled to look. Every entry is
        recorded so uninstall removes exactly these.
        """
        dovecot_conf = paths.config_dir("dovecot") / "dovecot.conf"
        postfix_conf = paths.config_dir("postfix")
        nginx_conf = paths.config_dir("nginx") / "nginx.conf"

        wrapped = {
            "dovecot": (self.bin("dovecot"), ["-c", str(dovecot_conf)]),
            "doveadm": (self.bin("dovecot", "doveadm"), ["-c", str(dovecot_conf)]),
            "doveconf": (self.bin("dovecot", "doveconf"), ["-c", str(dovecot_conf)]),
            "postfix": (self.bin("postfix", "postfix"), ["-c", str(postfix_conf)]),
            "postconf": (self.bin("postfix", "postconf"), ["-c", str(postfix_conf)]),
            "postmap": (self.bin("postfix", "postmap"), ["-c", str(postfix_conf)]),
            "nginx": (
                self.bin("nginx"),
                ["-p", self.bin("nginx", "prefix"), "-c", str(nginx_conf)],
            ),
        }
        plain = {
            "acmetool": self.bin("acmetool"),
            "filtermail": self.bin("filtermail"),
            "chatmail-turn": self.bin("chatmail-turn"),
            "iroh-relay": self.bin("iroh-relay"),
            "cmrelay": str(paths.CLI),
        }

        self.j.ensure_directory("tools", paths.LOCAL_BIN)
        names = []
        for name, (target, args) in wrapped.items():
            self.put_wrapper(paths.LOCAL_BIN / name, target, args)
            names.append(name)
        for name, target in plain.items():
            link = paths.LOCAL_BIN / name
            if link.exists() and not link.is_symlink():
                # cmdeploy's own binary on a taken-over host stays usable
                self.out.detail(f"leaving {link} alone: not a symlink")
                continue
            self.j.symlink("tools", link, target)
            names.append(name)

        self.j.write("tools", paths.LINKED_TOOLS, "\n".join(sorted(names)) + "\n")
        self.put_profile_script()

    def put_profile_script(self):
        """Put cmrelay's bin directory on root's PATH, appended not prepended.

        The tools that must win are in /usr/local/bin, which already precedes
        /usr/bin. Only login shells read this; every unit names a store path.
        """
        if not installer.write_profile_script(self.j):
            self.out.detail(
                f"{paths.PROFILE_D} does not exist; not adding {paths.BIN} to PATH"
            )

    def put_wrapper(self, link: Path, target: str, args: list):
        if link.is_symlink():
            self.j.remove_file("tools", link)
        self.j.write(
            "tools",
            link,
            "#!/bin/sh\n"
            "# written by cmrelay; regenerated on every run\n"
            f'exec {target} {" ".join(args)} "$@"\n',
            mode=0o755,
            detail=f"-> {target}",
        )

    # GithashDeployer

    def configure_version_marker(self):
        """The relay commit, in the format cmdeploy and its test suite expect."""
        self.j.write("cmrelay", paths.VERSION_MARKER, f"{self.bundle.relay_commit}\n")
