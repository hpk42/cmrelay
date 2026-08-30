"""The effects of relay's deployers, reproduced without pyinfra or apt.

The pyinfra deployer modules are reimplemented rather than vendored,
because they are the part that would rot. The order below follows
cmdeploy/src/cmdeploy/deployers.py so the two can be read side by side, and
the comments name the deployer each block mirrors.

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

from . import paths, render, systemd, users, www
from .version import __version__

# Matches ChatmailVenvDeployer.units; the timers own the last two.
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

FILTERMAIL_UNITS = ["filtermail", "filtermail-incoming", "filtermail-transport"]

# The five daemons Debian supplies units for and nixpkgs does not.
AUTHORED_UNITS = ["postfix", "dovecot", "nginx", "unbound", "opendkim"]

DKIM_SELECTOR = "opendkim"

# opendkim-genkey defaults to a weak 1024.
DKIM_KEY_BITS = 2048

# Below this, deploy asks acmetool to reach the network; otherwise never.
CERT_GRACE_SECONDS = 604800

# Started before anything else, because acmetool answers the HTTP challenge
# and dovecot, postfix and nginx will not start without the certificate.
ACME_UNITS = (
    "acmetool-redirector.service",
    "acmetool-reconcile.service",
    "acmetool-reconcile.timer",
)


# `postfix set-permissions` appends its own idea of the directory layout
# after this line. Carrying those lines forward is what stops main.cf being
# rewritten twice on every deploy, which would restart postfix each time.
STORE_MARKER = "# --- written by cmrelay: store layout ---"

CMRELAY_SETTINGS = ("config_directory", "queue_directory", "data_directory")


def postfix_own_settings(conf_dir: Path) -> str:
    """The settings postfix itself has written into main.cf, kept verbatim."""
    main_cf = Path(conf_dir) / "main.cf"
    if not main_cf.is_file():
        return ""
    _, marker, tail = main_cf.read_text().partition(STORE_MARKER)
    if not marker:
        return ""
    return "".join(
        line
        for line in tail.splitlines(keepends=True)
        if line.strip() and line.split("=", 1)[0].strip() not in CMRELAY_SETTINGS
    )


class DeployError(Exception):
    pass


class Deployment:
    """One deploy run against one host."""

    def __init__(
        self,
        bundle,
        config,
        out,
        journal,
        disable_mail=False,
        reconcile=False,
    ):
        self.bundle = bundle
        self.config = config
        self.out = out
        self.j = journal
        self.disable_mail = disable_mail
        self.reconcile = reconcile
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
        self.configure_filtermail()
        self.configure_journald()
        self.configure_unbound()
        self.configure_turn()
        self.configure_iroh()
        self.configure_tls()
        self.configure_website()
        self.configure_chatmaild()
        self.configure_mtasts()
        if not self.config.ipv4_relay:
            self.configure_opendkim()
        # Dovecot before postfix: it creates the authentication socket
        # postfix needs.
        self.configure_dovecot()
        self.configure_postfix()
        self.configure_fcgiwrap()
        self.configure_nginx()
        self.configure_mtail()
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

    # FiltermailDeployer

    def configure_filtermail(self):
        for name in FILTERMAIL_UNITS:
            self.unit_from_template(
                "filtermail",
                f"systemd/{name}.service.j2",
                bin_path=self.bin("filtermail"),
                config_path=str(paths.INI),
            )

    # JournaldDeployer

    def configure_journald(self):
        # guest: the distribution ships it and operators put retention in it
        self.j.copy(
            "journald",
            self.vendor("config/journald.conf"),
            paths.JOURNALD_CONF,
            guest=True,
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
        resolv = paths.RESOLV_CONF
        first = ""
        if resolv.is_file():
            for line in resolv.read_text(errors="replace").splitlines():
                fields = line.split("#", 1)[0].split()
                if len(fields) >= 2 and fields[0] == "nameserver":
                    first = fields[1]
                    break
        if first == "127.0.0.1":
            return
        # trailing newline: a SolusVM bug needs it
        self.j.write("unbound", resolv, "nameserver 127.0.0.1\nnameserver 9.9.9.9\n")

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
            self.configure_acmetool()
        elif mode == "self":
            self.configure_selfsigned()
        elif mode == "external":
            self.configure_external_tls()
        else:
            raise DeployError(f"Unknown tls_cert_mode: {mode}")

    def tls_domains(self) -> list:
        bare = self.config.mail_domain_bare
        return [bare, f"mta-sts.{bare}", f"www.{bare}"]

    def configure_acmetool(self):
        domains = self.tls_domains()
        acme = paths.ACME_STATE
        self.j.ensure_directory("acmetool", acme / "conf")
        self.j.ensure_directory("acmetool", acme / "desired")
        self.j.ensure_directory("acmetool", paths.ACME_HOOKS)

        self.put_file(
            "acmetool",
            "config/acmetool/acmetool.hook",
            paths.ACME_HOOKS / "nginx",
            mode=0o755,
        )
        self.j.remove_file("acmetool", paths.UPSTREAM_ACME_HOOK)

        self.put_template(
            "acmetool",
            "config/acmetool/response-file.yaml.j2",
            acme / "conf" / "responses",
            email=self.config.acme_email,
        )
        self.put_template(
            "acmetool", "config/acmetool/target.yaml.j2", acme / "conf" / "target"
        )
        for stale in sorted((acme / "desired").glob(f"{domains[0]}-*")):
            self.j.remove_file("acmetool", stale)
        self.put_template(
            "acmetool",
            "config/acmetool/desired.yaml.j2",
            acme / "desired" / domains[0],
            domains=domains,
        )

        bin_path = self.bin("acmetool")
        self.unit_from_template(
            "acmetool", "systemd/acmetool-redirector.service.j2", bin_path=bin_path
        )
        self.unit_from_template(
            "acmetool", "systemd/acmetool-reconcile.service.j2", bin_path=bin_path
        )
        self.unit_verbatim("acmetool", "systemd/acmetool-reconcile.timer")

        # Without this the upstream hook directory applies, nothing reloads
        # after a renewal, and the relay serves an expired certificate. The
        # drop-in keeps the units themselves verbatim.
        for unit in ("acmetool-reconcile.service", "acmetool-redirector.service"):
            self.j.install_dropin(
                "acmetool",
                unit,
                "10-hooks-dir.conf",
                f"[Service]\nEnvironment=ACME_HOOKS_DIR={paths.ACME_HOOKS}\n",
            )

    def configure_selfsigned(self):
        cert = Path(self.config.tls_cert_path)
        key = Path(self.config.tls_key_path)
        if cert.exists():
            return
        self.j.ensure_directory("dovecot", cert.parent)
        self.j.ensure_directory("dovecot", key.parent, mode=0o700)
        domain = self.config.mail_domain_bare
        self.shell(
            self.bin("openssl"),
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-noenc",
            "-days",
            "36500",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            f"/CN={domain}",
            # end-entity only, so it cannot be used as a CA to sign others
            "-addext",
            "basicConstraints=critical,CA:FALSE",
            "-addext",
            "extendedKeyUsage=serverAuth,clientAuth",
            "-addext",
            f"subjectAltName=DNS:{domain},DNS:www.{domain},DNS:mta-sts.{domain}",
        )
        self.j.produced("dovecot", cert, "self-signed certificate")
        self.j.produced("nginx", key, "self-signed key")

    def configure_external_tls(self):
        for path in (self.config.tls_cert_path, self.config.tls_key_path):
            if not Path(path).exists():
                raise DeployError(f"External TLS file not found on server: {path}")
        self.unit_from_template(
            "external-tls",
            "systemd/tls-cert-reload.path.j2",
            cert_path=self.config.tls_cert_path,
        )
        self.unit_verbatim("external-tls", "systemd/tls-cert-reload.service")

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

    # OpendkimDeployer

    def configure_opendkim(self):
        opendkim_config = {
            "domain_name": self.config.mail_domain_bare,
            "opendkim_selector": DKIM_SELECTOR,
        }
        conf_dir = paths.config_dir("opendkim")
        self.j.ensure_directory("opendkim", conf_dir)
        self.put_template(
            "opendkim",
            "config/opendkim/opendkim.conf",
            conf_dir / "opendkim.conf",
            config=opendkim_config,
            trust_anchor_file=str(paths.UNBOUND_ANCHOR),
        )

        self.j.ensure_directory(
            "opendkim", paths.OPENDKIM_ETC, mode=0o750, owner=("opendkim", "opendkim")
        )

        self.j.ensure_directory(
            "opendkim", paths.DKIM_KEYS, mode=0o750, owner=("opendkim", "opendkim")
        )
        for name in ("KeyTable", "SigningTable"):
            self.put_template(
                "opendkim",
                f"config/opendkim/{name}",
                paths.DKIM_KEYS / name,
                config=opendkim_config,
            )
            self.j.chown("opendkim", paths.DKIM_KEYS / name, "opendkim", "opendkim")

        self.j.ensure_directory(
            "opendkim",
            paths.POSTFIX_SPOOL / "opendkim",
            mode=0o750,
            owner=("opendkim", "opendkim"),
        )

        private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
        if not private.exists():
            self.generate_dkim_key()

        self.j.install_dropin(
            "opendkim",
            "opendkim.service",
            "10-prevent-memory-leak.conf",
            self.vendor("systemd/opendkim-10-prevent-memory-leak.conf").read_text(),
        )

    def generate_dkim_key(self):
        """openssl rather than opendkim-genkey, which would need Perl."""
        self.out.detail(f"generating a DKIM key for {self.mail_domain}")
        private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
        self.shell(
            self.bin("openssl"), "genrsa", "-out", str(private), str(DKIM_KEY_BITS)
        )
        self.j.produced("opendkim", private, f"{DKIM_KEY_BITS}-bit DKIM key")
        self.j.chmod("opendkim", private, 0o600)
        self.j.chown("opendkim", private, "opendkim", "opendkim")
        self.write_dkim_record(private)

    def write_dkim_record(self, private: Path):
        """The .txt opendkim-genkey leaves beside the key, for the operator."""
        record = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.txt"
        pubkey = self.dkim_public_key(private)
        self.j.write(
            "opendkim",
            record,
            f'{DKIM_SELECTOR}._domainkey\tIN\tTXT\t( "v=DKIM1; k=rsa; "\n'
            f'\t  "p={pubkey}" )'
            f"  ; ----- DKIM key {DKIM_SELECTOR} for {self.mail_domain}\n",
        )
        self.j.chown("opendkim", record, "opendkim", "opendkim")

    def dkim_public_key(self, private: Path) -> str:
        """The base64 body of the public key, the way relay extracts it."""
        result = self.shell(self.bin("openssl"), "rsa", "-in", str(private), "-pubout")
        return "".join(
            line for line in result.stdout.splitlines() if not line.startswith("-")
        )

    # DovecotDeployer

    def configure_dovecot(self):
        conf_dir = paths.config_dir("dovecot")
        self.j.ensure_directory("dovecot", conf_dir)

        self.put_template(
            "dovecot",
            "config/dovecot/dovecot.conf.j2",
            conf_dir / "dovecot.conf",
            config=self.config,
            debug=False,
            disable_ipv6=self.config.disable_ipv6,
            **self.template_vars(),
        )
        self.put_file("dovecot", "config/dovecot/auth.conf", conf_dir / "auth.conf")
        self.put_file(
            "dovecot",
            "config/dovecot/push_notification.lua",
            conf_dir / "push_notification.lua",
        )

        self.set_inotify_limits()
        self.ensure_timezone_env()

        self.j.install_dropin(
            "dovecot",
            "dovecot.service",
            "10_restart.conf",
            self.vendor("systemd/10_restart_on_failure.conf").read_text(),
        )

        if "dovecot" in self.j:
            self.shell(
                self.bin("dovecot", "doveconf"),
                "-c",
                str(conf_dir / "dovecot.conf"),
                "-n",
            )

    def ensure_timezone_env(self):
        """dovecot wants TZ set, and reads it from /etc/environment.

        Appended rather than rewritten, because the host owns this file.
        """
        path = paths.ENVIRONMENT
        line = "TZ=:/etc/localtime"
        existing = path.read_text() if path.is_file() else ""
        if any(entry.strip() == line for entry in existing.splitlines()):
            return
        body = existing if existing.endswith("\n") or not existing else existing + "\n"
        self.j.write("dovecot", path, f"{body}{line}\n")

    def set_inotify_limits(self):
        """Recommended by dovecot; not settable inside an unprivileged container."""
        wanted = False
        for name in ("max_user_instances", "max_user_watches"):
            key = f"fs.inotify.{name}"
            path = paths.PROC_INOTIFY / name
            try:
                current = int(path.read_text().strip())
            except OSError:
                continue
            if current > 65534:
                continue
            if self.j.sysctl(path, "65535\n"):
                wanted = True
                continue
            self.out.warn(
                f"dovecot: sysctl {key}={current}, should be >65534"
                " for production setups (cannot be set here)"
            )
        if wanted:
            self.j.write(
                "dovecot",
                paths.SYSCTL_INOTIFY,
                "fs.inotify.max_user_instances = 65535\n"
                "fs.inotify.max_user_watches = 65535\n",
            )

    # PostfixDeployer

    def configure_postfix(self):
        conf_dir = paths.config_dir("postfix")
        self.j.ensure_directory("postfix", conf_dir)
        tvars = self.template_vars()

        # One write, not a template write followed by an append: two writes
        # of the same file would report a change on every deploy and so
        # defeat every gate that asks whether postfix configuration moved.
        main_cf = render.render_template(
            self.vendor("config/postfix/main.cf.j2"),
            config=self.config,
            disable_ipv6=self.config.disable_ipv6,
            **tvars,
        )
        self.j.write(
            "postfix",
            conf_dir / "main.cf",
            main_cf + self.store_layout(conf_dir) + postfix_own_settings(conf_dir),
        )
        self.put_template(
            "postfix",
            "config/postfix/master.cf.j2",
            conf_dir / "master.cf",
            config=self.config,
            debug=False,
            **tvars,
        )
        for name in ("submission_header_cleanup", "lmtp_header_cleanup", "login_map"):
            self.put_file("postfix", f"config/postfix/{name}", conf_dir / name)

        self.write_postfix_store_settings(conf_dir)

        # No postmap: postfix reads a regexp table as text at lookup time,
        # and only the indexed types are compiled. test_render.py asserts the
        # declaration is still regexp, so a change to an indexed type fails
        # there rather than silently needing this back.
        self.put_file(
            "postfix",
            "config/postfix/smtp_tls_policy_map",
            conf_dir / "smtp_tls_policy_map",
        )

        self.ensure_aliases()

        # dovecot binds its sockets inside postfix's queue directory and
        # starts first, so the tree has to exist before any service does.
        # 262 ms, hence the gate.
        if "postfix" in self.j or self.spool_created:
            self.shell(
                self.bin("postfix", "postfix"), "-c", str(conf_dir), "set-permissions"
            )
            self.j.produced("postfix", paths.POSTFIX_SPOOL, "postfix set-permissions")

        self.j.install_dropin(
            "postfix",
            "postfix.service",
            "10_restart.conf",
            self.vendor("systemd/10_restart_on_failure.conf").read_text(),
        )

        if "postfix" in self.j:
            result = self.shell(
                self.bin("postfix", "postconf"), "-c", str(conf_dir), check=False
            )
            if result.stderr.strip():
                raise DeployError(f"postfix configuration is invalid:\n{result.stderr}")

    @staticmethod
    def store_layout(conf_dir: Path) -> str:
        """The directory settings a store-installed postfix cannot infer.

        queue_directory has to be /var/spool/postfix: relay's dovecot and
        opendkim sockets are queue-relative.
        """
        settings = {
            "config_directory": str(conf_dir),
            "queue_directory": str(paths.POSTFIX_SPOOL),
            "data_directory": str(paths.POSTFIX_LIB / "data"),
        }
        body = "\n".join(f"{k} = {v}" for k, v in settings.items())
        return f"\n{STORE_MARKER}\n{body}\n"

    def write_postfix_store_settings(self, conf_dir: Path):
        """The directories and the group postfix needs before it starts."""
        users.ensure_group("postdrop")
        # set-permissions fixes data/ but not its parent, without which
        # mail_owner cannot traverse in and master(8) dies on its lock file.
        self.j.ensure_directory("postfix", paths.POSTFIX_LIB, mode=0o755)
        self.j.ensure_directory(
            "postfix",
            paths.POSTFIX_LIB / "data",
            mode=0o700,
            owner=("postfix", "root"),
        )
        created = self.j.ensure_directory("postfix", paths.POSTFIX_SPOOL, mode=0o755)
        self.spool_created = created is not None and created.kind == "+"
        # postfix looks for postfix-files beside main.cf when it sets
        # permissions; nixpkgs keeps it in the package.
        self.j.symlink(
            "postfix",
            conf_dir / "postfix-files",
            Path(self.bin("postfix", "meta_directory")) / "postfix-files",
        )

    def ensure_aliases(self):
        """main.cf points alias_maps at /etc/aliases; a bare host may lack it."""
        aliases = paths.ALIASES
        if not aliases.exists():
            self.j.write("postfix", aliases, "postmaster: root\nroot: root\n")
        db = paths.ALIASES_DB
        if not db.exists() or db.stat().st_mtime < aliases.stat().st_mtime:
            self.shell(
                self.bin("postfix", "postalias"),
                "-c",
                str(paths.config_dir("postfix")),
                f"hash:{paths.ALIASES}",
                check=False,
            )
            self.j.produced("postfix", db, "rebuilt from /etc/aliases")

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

    # NginxDeployer

    def configure_nginx(self):
        conf_dir = paths.config_dir("nginx")
        self.j.ensure_directory("nginx", conf_dir)
        tvars = self.template_vars()

        self.put_template(
            "nginx",
            "config/nginx/nginx.conf.j2",
            conf_dir / "nginx.conf",
            config=self.config,
            disable_ipv6=self.config.disable_ipv6,
            **tvars,
        )
        self.put_template(
            "nginx",
            "config/nginx/autoconfig.xml.j2",
            paths.WWW_ROOT / ".well-known/autoconfig/mail/config-v1.1.xml",
            config=self.config,
        )
        self.put_template(
            "nginx",
            "config/nginx/mta-sts.txt.j2",
            paths.WWW_ROOT / ".well-known/mta-sts.txt",
            config=self.config,
        )

        # nginx wants its temp and log directories to exist before it starts.
        for sub in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
            self.j.ensure_directory("nginx", paths.NGINX_LIB / sub)
        self.j.ensure_directory("nginx", paths.NGINX_LOG)
        self.j.ensure_directory("nginx", paths.WWW_ROOT / ".well-known/acme-challenge")

        self.install_newemail_cgi()

    def install_newemail_cgi(self):
        """nginx runs chatmaild's newemail.py through fcgiwrap.

        From the installed chatmaild, which travels with its interpreter.
        """
        source = self.find_chatmaild_resource("newemail.py")
        self.j.ensure_directory("nginx", paths.CGI_BIN)
        target = paths.CGI_BIN / "newemail.py"
        body = source.read_text()

        # Twice: the shebang and CONFIG_PATH. Rewriting only the shebang
        # gives a CGI that starts and then reads an ini that is not there.
        if body.startswith("#!"):
            body = body.split("\n", 1)[1]
        old_config = 'CONFIG_PATH = "/usr/local/lib/chatmaild/chatmail.ini"'
        if old_config not in body:
            raise DeployError(
                "chatmaild's newemail.py no longer sets CONFIG_PATH the way"
                " cmrelay rewrites it; account creation would read the wrong"
                " configuration"
            )
        body = body.replace(old_config, f'CONFIG_PATH = "{paths.INI}"')

        shebang = f"#!{self.bin('python')}\n"
        self.j.write("nginx", target, shebang + body, mode=0o755)

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

    # MtailDeployer

    def configure_mtail(self):
        """Nothing at all unless mtail is wanted; the binary is the operator's."""
        if not self.config.mtail_address:
            return

        progs_dir = paths.config_dir("mtail")
        self.j.ensure_directory("mtail", progs_dir)
        self.unit_from_template(
            "mtail",
            "systemd/mtail.service.j2",
            address=self.config.mtail_address,
            port=3903,
            bin_path=str(paths.MTAIL),
            progs_dir=str(progs_dir),
        )

        for prog in sorted((self.bundle.vendor / "config/mtail").glob("*.mtail")):
            self.j.copy("mtail", prog, progs_dir / prog.name)
        # In the closure by the same hash relay downloads it under.
        self.j.copy(
            "mtail",
            Path(self.bundle.resolve(self.bin("filtermail", "mtail_program"))),
            progs_dir / "filtermail.mtail",
        )

        if "mtail" in self.j:
            self.validate_mtail_programs(progs_dir)

    def validate_mtail_programs(self, progs_dir: Path):
        """Fail the deploy rather than leave mtail crash-looping."""
        self.shell(
            "timeout",
            "30",
            str(paths.MTAIL),
            "--compile_only",
            "--one_shot",
            "--progs",
            str(progs_dir),
            "--logs",
            "/dev/null",
            "--address",
            "127.0.0.1",
            "--port",
            "0",
        )

    # tools on the operator's PATH

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
            self.j.symlink("tools", paths.LOCAL_BIN / name, target)
            names.append(name)

        self.j.write("tools", paths.LINKED_TOOLS, "\n".join(sorted(names)) + "\n")
        self.put_profile_script()

    def put_profile_script(self):
        """Put cmrelay's bin directory on root's PATH, appended not prepended.

        The tools that must win are in /usr/local/bin, which already precedes
        /usr/bin. Only login shells read this; every unit names a store path.
        """
        if not paths.PROFILE_D.is_dir():
            self.out.detail(
                f"{paths.PROFILE_D} does not exist; not adding {paths.BIN} to PATH"
            )
            return
        self.j.write(
            "tools",
            paths.PROFILE_SCRIPT,
            "# written by cmrelay; removed by `cmrelay uninstall`\n"
            'case ":$PATH:" in\n'
            f"    *:{paths.BIN}:*) ;;\n"
            f'    *) PATH="$PATH:{paths.BIN}" ;;\n'
            "esac\n"
            "export PATH\n",
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
        """The marker cmdeploy writes from its working tree.

        cmrelay writes its own version and the relay commit vendor.lock names.
        """
        self.j.write(
            "cmrelay",
            paths.VERSION_MARKER,
            f"{self.bundle.relay_commit}\n# installed by cmrelay {__version__}\n",
        )

    def install_daemon_units(self):
        for name in AUTHORED_UNITS:
            source = self.bundle.units / f"{name}.service"
            content = source.read_text().format(**self.daemon_unit_vars())
            self.j.install_unit(
                name, f"{name}.service", content, detail=self.component_label(name)
            )

    def component_label(self, name: str) -> str:
        try:
            return f"{name} {self.bundle.version(name)}"
        except Exception:  # noqa: BLE001 - a label is not worth failing a deploy
            return ""

    def daemon_unit_vars(self) -> dict:
        return {
            "postfix_bin": self.bin("postfix", "postfix"),
            "postfix_config_dir": str(paths.config_dir("postfix")),
            "dovecot_bin": self.bin("dovecot"),
            "doveadm_bin": self.bin("dovecot", "doveadm"),
            "dovecot_config": str(paths.config_dir("dovecot") / "dovecot.conf"),
            "nginx_bin": self.bin("nginx"),
            "nginx_prefix": self.bin("nginx", "prefix"),
            "nginx_config": str(paths.config_dir("nginx") / "nginx.conf"),
            "unbound_bin": self.bin("unbound"),
            "unbound_anchor_seed": str(paths.UNBOUND_ANCHOR_SEED),
            "unbound_config": str(paths.config_dir("unbound") / "unbound.conf"),
            "opendkim_bin": self.bin("opendkim"),
            "opendkim_config": str(paths.config_dir("opendkim") / "opendkim.conf"),
            "fcgiwrap_bin": self.bin("fcgiwrap"),
        }

    # activation

    def plan(self) -> systemd.Plan:
        """The unit state this configuration intends, as data."""
        plan = systemd.Plan()
        plan.reset_failed.append("unbound.service")
        plan.service("unbound.service", restart="unbound" in self.j)
        plan.units(["turnserver.service"], restart="turnserver" in self.j)

        iroh = bool(self.config.enable_iroh_relay)
        plan.service(
            "iroh-relay.service",
            running=iroh,
            enabled=iroh,
            restart="iroh" in self.j,
        )
        self.plan_tls(plan)
        self.plan_chatmaild(plan)
        plan.service("mta-sts-daemon.service", running=False, enabled=False)
        if not self.config.ipv4_relay:
            plan.service("opendkim.service", restart="opendkim" in self.j)
        plan.units(FILTERMAIL_UNITS, restart="filtermail" in self.j)

        active = not self.disable_mail
        for name in ("dovecot", "postfix"):
            plan.service(
                f"{name}.service",
                running=active,
                enabled=active,
                restart=name in self.j,
            )
        plan.service("fcgiwrap.socket", restart="fcgiwrap" in self.j)
        plan.service("fcgiwrap.service", restart="fcgiwrap" in self.j)
        plan.service("nginx.service", restart="nginx" in self.j)

        # No unit exists unless mtail was configured, so there is nothing to
        # disable on a host that never asked for one.
        if self.config.mtail_address:
            plan.service("mtail.service", restart="mtail" in self.j)
        plan.service(
            "systemd-journald.service", enabled=None, restart="journald" in self.j
        )
        return plan

    def plan_chatmaild(self, plan):
        for unit in CHATMAILD_UNITS:
            basename = unit if "." in unit else f"{unit}.service"
            # expire and fsreport are started by their timers, not directly
            enabled = unit not in TIMER_DRIVEN
            plan.service(
                basename,
                running=enabled,
                enabled=enabled,
                restart=enabled and "chatmaild" in self.j,
            )

    def plan_tls(self, plan):
        mode = self.config.tls_cert_mode
        if mode == "acme":
            plan.service("acmetool-redirector.service")
            plan.service("acmetool-reconcile.service", running=False, enabled=False)
            plan.service("acmetool-reconcile.timer")
        elif mode == "external":
            plan.service("tls-cert-reload.path")

    def mask_resolvers(self, states) -> list:
        """Mask the resolver managers competing with unbound for port 53.

        Only the ones not already masked, so a re-deploy spends nothing.
        """
        masked = []
        for unit, state in sorted(states.items()):
            if not state.exists or state.is_masked:
                continue
            systemd.stop_disable_mask(unit, reload=False)
            masked.append(unit)
        return masked

    def needs_reconcile(self) -> bool:
        """Whether acmetool has to reach the network at all this run.

        The only network access on the deploy path; the reconcile timer owns
        renewals, so this runs only without a usable certificate.
        """
        if self.reconcile:
            return True
        cert = Path(self.config.tls_cert_path)
        if not cert.is_file():
            return True
        checkend = self.shell(
            self.bin("openssl"),
            "x509",
            "-checkend",
            str(CERT_GRACE_SECONDS),
            "-noout",
            "-in",
            str(cert),
            check=False,
        )
        if checkend.returncode:
            return True
        return not self.cert_covers(cert, self.tls_domains())

    def cert_covers(self, cert: Path, domains) -> bool:
        result = self.shell(
            self.bin("openssl"),
            "x509",
            "-noout",
            "-ext",
            "subjectAltName",
            "-in",
            str(cert),
            check=False,
        )
        names = {
            entry.strip().removeprefix("DNS:")
            for line in result.stdout.splitlines()
            for entry in line.split(",")
            if entry.strip().startswith("DNS:")
        }
        return set(domains) <= names

    def run_acmetool(self):
        self.out.detail(f"reconciling certificates for {', '.join(self.tls_domains())}")
        result = self.shell(
            self.bin("acmetool"),
            "--batch",
            "--xlog.severity=debug",
            f"--hooks={paths.ACME_HOOKS}",
            "reconcile",
            check=False,
        )
        if result.returncode:
            self.out.warn(
                "acmetool could not obtain a certificate. That needs DNS"
                " already pointing at this host and the challenge port"
                " reachable from the internet:\n" + result.stderr.strip()[-2000:]
            )

    def verify(self) -> list:
        """Check that DKIM signing and TLS actually work, rather than assuming."""
        problems = []
        states = systemd.unit_states(
            ["opendkim.service", "dovecot.service", "postfix.service", "nginx.service"]
        )

        if not self.config.ipv4_relay:
            key = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
            if not key.is_file():
                problems.append(f"no DKIM key at {key}")
            elif (
                states.get("opendkim.service")
                and not states["opendkim.service"].is_running
            ):
                problems.append("opendkim.service is not running")

        for path in (self.config.tls_cert_path, self.config.tls_key_path):
            if not Path(path).is_file():
                problems.append(f"no TLS material at {path}")

        expected = (
            ["dovecot.service", "postfix.service"] if not self.disable_mail else []
        )
        for unit in [*expected, "nginx.service"]:
            state = states.get(unit)
            if state is not None and state.exists and not state.is_running:
                problems.append(f"{unit} is not running")
        return problems
