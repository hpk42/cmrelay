"""The effects of relay's deployers, reproduced without pyinfra or apt.

The pyinfra deployer modules are reimplemented rather than vendored, because
they are the part that would rot. The order below follows
cmdeploy/src/cmdeploy/deployers.py so the two can be read side by side, and
the comments name the deployer each block mirrors.

What does not carry over: apt.packages(), because the component is already in
the closure; Debian mechanics like policy-rc.d and the dovecot apt pin, which
mean nothing without apt; and binary paths, which are store paths here and
already template variables in every unit.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import paths, render, systemd, users
from .render import Changes, copy, ensure_directory, remove_file, write
from .version import __version__
from .website import build_website

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

# Debian patches acmetool to read this; upstream reads
# /usr/libexec/acme/hooks, so cmrelay has to say so.
ACME_HOOKS_DIR = "/etc/acme/hooks"

# opendkim-genkey defaults to 1024, which is weak by current practice.
DKIM_KEY_BITS = 2048


class DeployError(Exception):
    pass


class Deployment:
    """One install or upgrade run against one host."""

    def __init__(self, bundle, config, out, disable_mail=False):
        self.bundle = bundle
        self.config = config
        self.out = out
        self.disable_mail = disable_mail
        self.changes = Changes()

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

    def ca_path(self) -> str:
        """Where the host keeps its trusted CA bundle.

        A host path, not a store path: the store's own bundle would freeze
        the trust store at build time.
        """
        for candidate in ("/etc/ssl/certs", "/etc/pki/tls/certs"):
            if Path(candidate).is_dir():
                return candidate
        return "/etc/ssl/certs"

    def put_template(self, component, src, dest, **data):
        content = render.render_template(self.vendor(src), **data)
        return self.changes.note(component, write(Path(dest), content))

    def put_file(self, component, src, dest, mode=0o644, owner=None):
        return self.changes.note(
            component, copy(self.vendor(src), Path(dest), mode=mode, owner=owner)
        )

    def unit_from_template(self, component, src, name=None, **data):
        content = render.render_template(self.vendor(src), **data)
        name = name or Path(src).name.removesuffix(".j2")
        return self.changes.note(component, systemd.install_unit(name, content))

    def unit_verbatim(self, component, src, name=None):
        name = name or Path(src).name
        content = self.vendor(src).read_text()
        return self.changes.note(component, systemd.install_unit(name, content))

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
        ensure_directory(
            Path(self.config.mailboxes_dir), mode=0o700, owner=("vmail", "vmail")
        )
        users.verify_mailbox_ownership(self.config.mailboxes_dir)

        # Used by the auth proxy. https://wiki.debian.org/EtcMailName
        write(paths.MAILNAME, f"{self.mail_domain}\n", mode=0o644)

    # LegacyRemoveDeployer

    def remove_legacy(self):
        for path in (
            "/etc/cron.d/expunge",
            "/etc/cron.d/chatmail-metrics",
            "/var/www/html/metrics",
            "/etc/cron.d/acmetool",
        ):
            remove_file(Path(path))

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
        self.put_file("journald", "config/journald.conf", "/etc/systemd/journald.conf")

    # UnboundDeployer

    def configure_unbound(self):
        self.write_unbound_anchor_seed()
        # Dynamic resolver managers compete for /etc/resolv.conf. cmdeploy
        # purges resolvconf and masks systemd-resolved; cmrelay cannot purge
        # a package, so masking is all of it.
        for unit in ("systemd-resolved.service", "unbound-resolvconf.service"):
            if systemd.available():
                systemd.stop_disable_mask(unit)
                systemd.record_units(paths.DISABLED_UNITS, [unit])

        # Quad9 fallback, with the trailing newline a SolusVM bug needs.
        write(Path("/etc/resolv.conf"), "nameserver 127.0.0.1\nnameserver 9.9.9.9\n")

        conf_dir = paths.config_dir("unbound")
        drop_in_dir = conf_dir / "unbound.conf.d"
        ensure_directory(drop_in_dir)
        self.put_template(
            "unbound",
            "config/unbound/unbound.conf.j2",
            drop_in_dir / "chatmail.conf",
            disable_ipv6=self.config.disable_ipv6,
        )
        self.write_unbound_base(conf_dir, drop_in_dir)

    def write_unbound_base(self, conf_dir: Path, drop_in_dir: Path):
        """The top-level unbound.conf, which relay never supplies.

        relay writes only a drop-in, because Debian's package ships the main config,
        a chroot helper and the root anchor. A store unbound has none of that.
        /etc/unbound/unbound.conf.d is included deliberately: other tooling drops
        files there, and on a converted host it still holds the package's conffiles,
        which is why the anchor below is conditional.
        """
        ensure_directory(Path("/var/lib/unbound"), owner=("unbound", "unbound"))
        ensure_directory(Path("/etc/unbound/unbound.conf.d"))

        # Debian's unbound ships root-auto-trust-anchor-file.conf, setting
        # the very directive below, and two anchors for one zone is fatal:
        # "trust anchor for '.' presented twice" and unbound does not start.
        # Only reachable by taking a host over. Yielding rather than removing
        # their file, which is a dpkg conffile `cmdeploy run` must find.
        anchor_line = '    auto-trust-anchor-file: "/var/lib/unbound/root.key"\n'
        if self.anchor_already_configured():
            self.out.print(
                "  /etc/unbound/unbound.conf.d already sets the trust anchor;"
                " leaving it to do so"
            )
            anchor_line = ""

        self.changes.note(
            "unbound",
            write(
                conf_dir / "unbound.conf",
                "# written by cmrelay; regenerated on every run\n"
                "server:\n"
                '    username: "unbound"\n'
                '    directory: "/var/lib/unbound"\n'
                '    chroot: ""\n'
                '    pidfile: "/run/unbound.pid"\n' + anchor_line + "\n"
                f'include: "{drop_in_dir}/*.conf"\n'
                'include: "/etc/unbound/unbound.conf.d/*.conf"\n',
            ),
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
        self.changes.note(
            "turnserver", systemd.install_unit("turnserver.service", content)
        )

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

    def tls_domains(self) -> list[str]:
        bare = self.config.mail_domain_bare
        return [bare, f"mta-sts.{bare}", f"www.{bare}"]

    def configure_acmetool(self):
        domains = self.tls_domains()
        acme = paths.ACME_STATE
        ensure_directory(acme / "conf")
        ensure_directory(acme / "desired")
        ensure_directory(Path(ACME_HOOKS_DIR))

        self.put_file(
            "acmetool",
            "config/acmetool/acmetool.hook",
            f"{ACME_HOOKS_DIR}/nginx",
            mode=0o755,
        )
        remove_file(Path("/usr/lib/acme/hooks/nginx"))

        self.put_template(
            "acmetool",
            "config/acmetool/response-file.yaml.j2",
            acme / "conf" / "responses",
            email=self.config.acme_email,
        )
        self.put_template(
            "acmetool", "config/acmetool/target.yaml.j2", acme / "conf" / "target"
        )
        for stale in (acme / "desired").glob(f"{domains[0]}-*"):
            stale.unlink()
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
            systemd.install_dropin(
                unit,
                "10-hooks-dir.conf",
                f"[Service]\nEnvironment=ACME_HOOKS_DIR={ACME_HOOKS_DIR}\n",
            )

    def configure_selfsigned(self):
        cert = Path(self.config.tls_cert_path)
        key = Path(self.config.tls_key_path)
        if cert.exists():
            return
        ensure_directory(cert.parent)
        ensure_directory(key.parent, mode=0o700)
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
        self.changes.note("dovecot", True)
        self.changes.note("nginx", True)

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
        ensure_directory(paths.WWW_ROOT)
        try:
            built = build_website(self.bundle.vendor / "www", self.config)
        except Exception as exc:  # noqa: BLE001 - a broken page must not fail a deploy
            self.out.warn(f"web page build failed, skipping website: {exc}")
            return
        for src in sorted(built.iterdir()):
            if src.is_file():
                copy(src, paths.WWW_ROOT / src.name)

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
            self.changes.note("chatmaild", systemd.install_unit(basename, content))

    # MtastsDeployer

    def configure_mtasts(self):
        # postfix-mta-sts-resolver is dead. This is a pure removal step.
        remove_file(Path("/etc/mta-sts-daemon.yml"))
        render.remove_directory(Path("/usr/local/lib/postfix-mta-sts-resolver"))
        remove_file(Path("/etc/systemd/system/mta-sts-daemon.service"))

    # OpendkimDeployer

    def configure_opendkim(self):
        opendkim_config = {
            "domain_name": self.config.mail_domain_bare,
            "opendkim_selector": DKIM_SELECTOR,
        }
        conf_dir = paths.config_dir("opendkim")
        ensure_directory(conf_dir)
        self.put_template(
            "opendkim",
            "config/opendkim/opendkim.conf",
            conf_dir / "opendkim.conf",
            config=opendkim_config,
            trust_anchor_file="/var/lib/unbound/root.key",
        )

        remove_file(Path("/etc/opendkim/screen.lua"))
        remove_file(Path("/etc/opendkim/final.lua"))
        ensure_directory(
            Path("/etc/opendkim"), mode=0o750, owner=("opendkim", "opendkim")
        )

        ensure_directory(paths.DKIM_KEYS, mode=0o750, owner=("opendkim", "opendkim"))
        self.put_template(
            "opendkim",
            "config/opendkim/KeyTable",
            paths.DKIM_KEYS / "KeyTable",
            config=opendkim_config,
        )
        self.put_template(
            "opendkim",
            "config/opendkim/SigningTable",
            paths.DKIM_KEYS / "SigningTable",
            config=opendkim_config,
        )
        for path in (paths.DKIM_KEYS / "KeyTable", paths.DKIM_KEYS / "SigningTable"):
            shutil.chown(path, user="opendkim", group="opendkim")

        ensure_directory(
            paths.POSTFIX_SPOOL / "opendkim",
            mode=0o750,
            owner=("opendkim", "opendkim"),
        )

        private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
        if not private.exists():
            self.generate_dkim_key()

        systemd.install_dropin(
            "opendkim.service",
            "10-prevent-memory-leak.conf",
            self.vendor("systemd/opendkim-10-prevent-memory-leak.conf").read_text(),
        )

    def generate_dkim_key(self):
        """Make the DKIM keypair with openssl rather than opendkim-genkey.

        opendkim-genkey is the only thing in the closure that would need Perl, 55 MiB
        to run one command once per host, and the private key is the only artefact
        anything downstream depends on. relay already splits a public key across
        quoted strings, so 2048 bits needs nothing new.
        """
        self.out.print(f"Generating DKIM key for {self.mail_domain} ...")
        private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
        self.shell(
            self.bin("openssl"), "genrsa", "-out", str(private), str(DKIM_KEY_BITS)
        )
        os.chmod(private, 0o600)
        shutil.chown(private, user="opendkim", group="opendkim")
        self.write_dkim_record(private)

    def write_dkim_record(self, private: Path):
        """The .txt file opendkim-genkey leaves beside the key.

        Nothing reads it; it exists because an operator looking for their DNS record
        will look here.
        """
        record = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.txt"
        pubkey = self.dkim_public_key(private)
        self.changes.note(
            "opendkim",
            write(
                record,
                f'{DKIM_SELECTOR}._domainkey\tIN\tTXT\t( "v=DKIM1; k=rsa; "\n'
                f'\t  "p={pubkey}" )'
                f"  ; ----- DKIM key {DKIM_SELECTOR} for {self.mail_domain}\n",
            ),
        )
        shutil.chown(record, user="opendkim", group="opendkim")

    def dkim_public_key(self, private: Path) -> str:
        """The base64 body of the public key, the way relay extracts it."""
        result = self.shell(self.bin("openssl"), "rsa", "-in", str(private), "-pubout")
        return "".join(
            line for line in result.stdout.splitlines() if not line.startswith("-")
        )

    # DovecotDeployer

    def configure_dovecot(self):
        conf_dir = paths.config_dir("dovecot")
        ensure_directory(conf_dir)

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

        systemd.install_dropin(
            "dovecot.service",
            "10_restart.conf",
            self.vendor("systemd/10_restart_on_failure.conf").read_text(),
        )

        if "dovecot" in self.changes:
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
        path = Path("/etc/environment")
        line = "TZ=:/etc/localtime"
        existing = path.read_text() if path.is_file() else ""
        if any(entry.strip() == line for entry in existing.splitlines()):
            return
        body = existing if existing.endswith("\n") or not existing else existing + "\n"
        self.changes.note("dovecot", write(path, f"{body}{line}\n"))

    def set_inotify_limits(self):
        """Recommended by dovecot; not settable inside an unprivileged container."""
        for name in ("max_user_instances", "max_user_watches"):
            key = f"fs.inotify.{name}"
            path = Path("/proc/sys/fs/inotify") / name
            try:
                current = int(path.read_text().strip())
            except OSError:
                continue
            if current > 65534:
                continue
            try:
                path.write_text("65535\n")
            except OSError:
                self.out.warn(
                    f"dovecot: sysctl {key}={current}, should be >65534"
                    " for production setups (cannot be set here)"
                )
                continue
            write(
                Path("/etc/sysctl.d/60-cmrelay-inotify.conf"),
                "fs.inotify.max_user_instances = 65535\n"
                "fs.inotify.max_user_watches = 65535\n",
            )

    # PostfixDeployer

    def configure_postfix(self):
        conf_dir = paths.config_dir("postfix")
        ensure_directory(conf_dir)
        tvars = self.template_vars()

        self.put_template(
            "postfix",
            "config/postfix/main.cf.j2",
            conf_dir / "main.cf",
            config=self.config,
            disable_ipv6=self.config.disable_ipv6,
            **tvars,
        )
        self.put_template(
            "postfix",
            "config/postfix/master.cf.j2",
            conf_dir / "master.cf",
            config=self.config,
            debug=False,
            **tvars,
        )
        for name in (
            "submission_header_cleanup",
            "lmtp_header_cleanup",
            "login_map",
        ):
            self.put_file("postfix", f"config/postfix/{name}", conf_dir / name)

        # The store layout has to be written before any postfix tool runs:
        # they all resolve their settings from config_directory, which
        # defaults to /etc/postfix and is not where cmrelay keeps them.
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
        # Debian does this in its postinst, so the ordering never shows there.
        self.shell(
            self.bin("postfix", "postfix"), "-c", str(conf_dir), "set-permissions"
        )

        systemd.install_dropin(
            "postfix.service",
            "10_restart.conf",
            self.vendor("systemd/10_restart_on_failure.conf").read_text(),
        )

        if "postfix" in self.changes:
            result = self.shell(
                self.bin("postfix", "postconf"),
                "-c",
                str(conf_dir),
                check=False,
            )
            if result.stderr.strip():
                raise DeployError(f"postfix configuration is invalid:\n{result.stderr}")

    def write_postfix_store_settings(self, conf_dir: Path):
        """The directory settings a store-installed postfix cannot infer.

        In a separate file appended to main.cf, so relay's template stays verbatim
        apart from its declared patches.
        """
        # nixpkgs' postfix already points its own directories at store
        # paths, so only state and config need saying. queue_directory has to
        # be /var/spool/postfix: relay's dovecot and opendkim sockets live
        # under it and are queue-relative by construction.
        settings = {
            "config_directory": str(conf_dir),
            "queue_directory": str(paths.POSTFIX_SPOOL),
            "data_directory": "/var/lib/postfix/data",
        }
        body = "\n".join(f"{k} = {v}" for k, v in settings.items())
        main_cf = conf_dir / "main.cf"
        existing = main_cf.read_text()
        marker = "\n# --- written by cmrelay: store layout ---\n"
        base = existing.split(marker)[0].rstrip("\n")
        self.changes.note("postfix", write(main_cf, f"{base}\n{marker}{body}\n"))

        users.ensure_group("postdrop")
        # set-permissions fixes data/ but not its parent, without which
        # mail_owner cannot traverse in and master(8) dies on its lock file.
        ensure_directory(Path("/var/lib/postfix"), mode=0o755)
        ensure_directory(
            Path("/var/lib/postfix/data"), mode=0o700, owner=("postfix", "root")
        )
        ensure_directory(paths.POSTFIX_SPOOL, mode=0o755)
        # postfix looks for postfix-files beside main.cf when it sets
        # permissions; nixpkgs keeps it in the package.
        link = conf_dir / "postfix-files"
        if not link.exists():
            link.symlink_to(
                Path(self.bin("postfix", "meta_directory")) / "postfix-files"
            )

    def ensure_aliases(self):
        """main.cf points alias_maps at /etc/aliases; a bare host may lack it."""
        aliases = Path("/etc/aliases")
        if not aliases.exists():
            write(aliases, "postmaster: root\nroot: root\n")
        db = Path("/etc/aliases.db")
        if not db.exists() or db.stat().st_mtime < aliases.stat().st_mtime:
            self.shell(
                self.bin("postfix", "postalias"),
                "-c",
                str(paths.config_dir("postfix")),
                "hash:/etc/aliases",
                check=False,
            )

    # FcgiwrapDeployer

    def configure_fcgiwrap(self):
        """Both units: the service is socket-activated, so the socket is not
        optional and installing only the service cannot start.
        """
        for name in ("fcgiwrap.socket", "fcgiwrap.service"):
            content = (
                (self.bundle.units / name)
                .read_text()
                .format(fcgiwrap_bin=self.bin("fcgiwrap"))
            )
            self.changes.note("fcgiwrap", systemd.install_unit(name, content))

    # NginxDeployer

    def configure_nginx(self):
        conf_dir = paths.config_dir("nginx")
        ensure_directory(conf_dir)
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
            ensure_directory(Path("/var/lib/nginx") / sub)
        ensure_directory(Path("/var/log/nginx"))
        ensure_directory(paths.WWW_ROOT / ".well-known/acme-challenge")

        self.install_newemail_cgi()

    def install_newemail_cgi(self):
        """nginx runs chatmaild's newemail.py through fcgiwrap.

        Taken from the installed chatmaild rather than the vendor set, because it
        travels with the interpreter that runs it.
        """
        source = self.find_chatmaild_resource("newemail.py")
        ensure_directory(paths.CGI_BIN)
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
        self.changes.note("nginx", write(target, shebang + body, mode=0o755))

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
        """The unit always, the programs only when mtail is actually wanted.

        Validating rules that will never be evaluated costs a subprocess.
        """
        progs_dir = paths.config_dir("mtail")
        ensure_directory(progs_dir)
        self.unit_from_template(
            "mtail",
            "systemd/mtail.service.j2",
            address=self.config.mtail_address or "127.0.0.1",
            port=3903,
            bin_path=self.bin("mtail"),
            progs_dir=str(progs_dir),
        )
        if not self.config.mtail_address:
            return

        for prog in sorted((self.bundle.vendor / "config/mtail").glob("*.mtail")):
            self.changes.note("mtail", copy(prog, progs_dir / prog.name))
        # In the closure by the same hash relay downloads it under.
        self.changes.note(
            "mtail",
            copy(
                Path(self.bundle.resolve(self.bin("filtermail", "mtail_program"))),
                progs_dir / "filtermail.mtail",
            ),
        )

        if "mtail" in self.changes:
            self.validate_mtail_programs(progs_dir)

    def validate_mtail_programs(self, progs_dir: Path):
        """Fail the install rather than leave mtail crash-looping on a bad rule."""
        self.shell(
            "timeout",
            "30",
            self.bin("mtail"),
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
    def anchor_already_configured(
        drop_ins=Path("/etc/unbound/unbound.conf.d"),
    ) -> bool:
        """Whether something in the distribution's drop-ins anchors a zone."""
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

        From then on the file is unbound's own, rewritten as the root rolls its key
        per RFC 5011. Guarding on absence keeps the self-healing without touching an
        anchor the daemon maintains perfectly well.
        """
        paths.BIN.mkdir(parents=True, exist_ok=True)
        anchor = "/var/lib/unbound/root.key"
        write(
            paths.UNBOUND_ANCHOR_SEED,
            "#!/bin/sh\n"
            "# written by cmrelay; regenerated on every run\n"
            f"test -s {anchor} && exit 0\n"
            f"exec {self.bin('unbound', 'unbound-anchor')} -a {anchor}\n",
            mode=0o755,
        )

    def link_host_tools(self):
        """Put the tools relay invokes by bare name where PATH will find them.

        A store path suffices for a unit file and for nothing else: relay's helpers
        and test suite run `doveadm quota get`, `dovecot --version` and
        `acmetool account-url`. dovecot's and postfix's get a wrapper rather than a
        symlink, because cmrelay's configuration is not where they were compiled to
        look; symlinking it into /etc/dovecot would cost the rollback, since that
        file is cmdeploy's. Every entry is recorded so uninstall removes exactly
        these.
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
            "mtail": self.bin("mtail"),
            "cmrelay": str(paths.CLI),
        }

        ensure_directory(paths.LOCAL_BIN)
        names = []
        for name, (target, args) in wrapped.items():
            self.put_wrapper(paths.LOCAL_BIN / name, target, args)
            names.append(name)
        for name, target in plain.items():
            self.put_link(paths.LOCAL_BIN / name, target)
            names.append(name)

        paths.STATE.mkdir(parents=True, exist_ok=True)
        write(paths.LINKED_TOOLS, "\n".join(sorted(names)) + "\n")
        self.put_profile_script()

    def put_profile_script(self):
        """Put cmrelay's own bin directory on root's PATH.

        Appended rather than prepended, so nothing here shadows a distribution tool
        of the same name; the ones that must win are in /usr/local/bin, which already
        precedes /usr/bin. Only login shells read this, which is why every unit names
        an absolute store path.
        """
        if not paths.PROFILE_D.is_dir():
            self.out.print(
                f"  {paths.PROFILE_D} does not exist; not adding {paths.BIN} to PATH"
            )
            return
        write(
            paths.PROFILE_SCRIPT,
            "# written by cmrelay; removed by `cmrelay uninstall`\n"
            f'case ":$PATH:" in\n'
            f"    *:{paths.BIN}:*) ;;\n"
            f'    *) PATH="$PATH:{paths.BIN}" ;;\n'
            "esac\n"
            "export PATH\n",
            mode=0o644,
        )

    def put_wrapper(self, link: Path, target: str, args: list[str]):
        if link.is_symlink():
            link.unlink()
        write(
            link,
            "#!/bin/sh\n"
            "# written by cmrelay; regenerated on every run\n"
            f'exec {target} {" ".join(args)} "$@"\n',
            mode=0o755,
        )

    def put_link(self, link: Path, target: str):
        if link.is_symlink() and link.readlink() == Path(target):
            return
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    # GithashDeployer

    def configure_version_marker(self):
        """The marker cmdeploy writes from its working tree.

        cmrelay installs a released artefact rather than a checkout, so it writes its
        own version and the relay commit vendor.lock names. `install --take-over`
        reads it back to report what the host ran.
        """
        write(
            paths.VERSION_MARKER,
            f"{self.bundle.relay_commit}\n# installed by cmrelay {__version__}\n",
        )

    def install_daemon_units(self):
        for name in AUTHORED_UNITS:
            source = self.bundle.units / f"{name}.service"
            content = source.read_text().format(**self.daemon_unit_vars())
            self.changes.note(name, systemd.install_unit(f"{name}.service", content))

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

    def activate(self):
        systemd.daemon_reload()
        if not systemd.available():
            self.out.warn("no systemd here; leaving every service alone")
            return

        self.activate_unbound()
        self.activate_units(["turnserver.service"], "turnserver")
        systemd.ensure_service(
            "iroh-relay.service",
            running=bool(self.config.enable_iroh_relay),
            enabled=bool(self.config.enable_iroh_relay),
            restart="iroh" in self.changes,
        )
        self.activate_tls()
        self.activate_chatmaild()
        systemd.ensure_service("mta-sts-daemon.service", running=False, enabled=False)
        if not self.config.ipv4_relay:
            systemd.ensure_service(
                "opendkim.service", restart="opendkim" in self.changes
            )
        self.activate_units(FILTERMAIL_UNITS, "filtermail")

        active = not self.disable_mail
        systemd.ensure_service(
            "dovecot.service",
            running=active,
            enabled=active,
            restart="dovecot" in self.changes,
        )
        systemd.ensure_service(
            "postfix.service",
            running=active,
            enabled=active,
            restart="postfix" in self.changes,
        )
        systemd.ensure_service("fcgiwrap.socket", restart="fcgiwrap" in self.changes)
        systemd.ensure_service("fcgiwrap.service", restart="fcgiwrap" in self.changes)
        systemd.ensure_service("nginx.service", restart="nginx" in self.changes)

        mtail_on = bool(self.config.mtail_address)
        systemd.ensure_service(
            "mtail.service",
            running=mtail_on,
            enabled=mtail_on,
            restart="mtail" in self.changes,
        )
        systemd.ensure_service(
            "systemd-journald.service", restart="journald" in self.changes
        )

    def activate_unbound(self):
        systemd.systemctl("reset-failed", "unbound.service", check=False)
        systemd.ensure_service("unbound.service", restart="unbound" in self.changes)

    def activate_units(self, names, component):
        for name in names:
            basename = name if "." in name else f"{name}.service"
            systemd.ensure_service(basename, restart=component in self.changes)

    def activate_chatmaild(self):
        for unit in CHATMAILD_UNITS:
            basename = unit if "." in unit else f"{unit}.service"
            # expire and fsreport are started by their timers, not directly
            enabled = unit not in TIMER_DRIVEN
            systemd.ensure_service(
                basename,
                running=enabled,
                enabled=enabled,
                restart=enabled and "chatmaild" in self.changes,
            )

    def activate_tls(self):
        mode = self.config.tls_cert_mode
        if mode == "acme":
            systemd.ensure_service("acmetool-redirector.service")
            systemd.ensure_service(
                "acmetool-reconcile.service", running=False, enabled=False
            )
            systemd.ensure_service("acmetool-reconcile.timer")
            self.out.print(
                f"Reconciling certificates for {', '.join(self.tls_domains())} ..."
            )
            result = self.shell(
                self.bin("acmetool"),
                "--batch",
                "--xlog.severity=debug",
                f"--hooks={ACME_HOOKS_DIR}",
                "reconcile",
                check=False,
            )
            if result.returncode:
                self.out.warn(
                    "acmetool could not obtain a certificate. That needs DNS"
                    " already pointing at this host and the challenge port"
                    " reachable from the internet:\n" + result.stderr.strip()[-2000:]
                )
        elif mode == "external":
            systemd.ensure_service("tls-cert-reload.path")
