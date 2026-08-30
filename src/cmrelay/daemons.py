"""The deployers relay keeps in per-component modules, as functions over `d`.

`cmrelay.deploy` holds the Deployment class and the run order; this module
holds the bulk of the mirrored deployers, in deployers.py's all_deployers
order: filtermail, the TLS modes, opendkim, dovecot, postfix, nginx and
mtail. Every function takes the running Deployment as `d` and writes
through `d.j`.
"""

from pathlib import Path

from . import fsops, paths, render, users


class DeployError(Exception):
    pass


# FiltermailDeployer

FILTERMAIL_UNITS = ["filtermail", "filtermail-incoming", "filtermail-transport"]


def configure_filtermail(d):
    for name in FILTERMAIL_UNITS:
        d.unit_from_template(
            "filtermail",
            f"systemd/{name}.service.j2",
            bin_path=d.bin("filtermail"),
            config_path=str(paths.INI),
        )


# the TLS deployers


def configure_acmetool(d):
    domains = d.tls_domains()
    acme = paths.ACME_STATE
    d.j.ensure_directory("acmetool", acme / "conf")
    d.j.ensure_directory("acmetool", acme / "desired")
    d.j.ensure_directory("acmetool", paths.ACME_HOOKS)

    d.put_file(
        "acmetool",
        "config/acmetool/acmetool.hook",
        paths.ACME_HOOKS / "nginx",
        mode=0o755,
    )
    d.j.remove_file("acmetool", paths.UPSTREAM_ACME_HOOK)

    d.put_template(
        "acmetool",
        "config/acmetool/response-file.yaml.j2",
        acme / "conf" / "responses",
        email=d.config.acme_email,
    )
    d.put_template(
        "acmetool", "config/acmetool/target.yaml.j2", acme / "conf" / "target"
    )
    for stale in sorted((acme / "desired").glob(f"{domains[0]}-*")):
        d.j.remove_file("acmetool", stale)
    d.put_template(
        "acmetool",
        "config/acmetool/desired.yaml.j2",
        acme / "desired" / domains[0],
        domains=domains,
    )

    bin_path = d.bin("acmetool")
    d.unit_from_template(
        "acmetool", "systemd/acmetool-redirector.service.j2", bin_path=bin_path
    )
    d.unit_from_template(
        "acmetool", "systemd/acmetool-reconcile.service.j2", bin_path=bin_path
    )
    d.unit_verbatim("acmetool", "systemd/acmetool-reconcile.timer")

    # Without this the upstream hook directory applies, nothing reloads
    # after a renewal, and the relay serves an expired certificate. The
    # drop-in keeps the units themselves verbatim.
    for unit in ("acmetool-reconcile.service", "acmetool-redirector.service"):
        d.j.install_dropin(
            "acmetool",
            unit,
            "10-hooks-dir.conf",
            f"[Service]\nEnvironment=ACME_HOOKS_DIR={paths.ACME_HOOKS}\n",
        )


def configure_selfsigned(d):
    cert = Path(d.config.tls_cert_path)
    key = Path(d.config.tls_key_path)
    if cert.exists():
        return
    d.j.ensure_directory("dovecot", cert.parent)
    d.j.ensure_directory("dovecot", key.parent, mode=0o700)
    domain = d.config.mail_domain_bare
    d.shell(
        d.bin("openssl"),
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
    d.j.produced("dovecot", cert, "self-signed certificate")
    d.j.produced("nginx", key, "self-signed key")


def configure_external_tls(d):
    for path in (d.config.tls_cert_path, d.config.tls_key_path):
        if not Path(path).exists():
            raise DeployError(f"External TLS file not found on server: {path}")
    d.unit_from_template(
        "external-tls",
        "systemd/tls-cert-reload.path.j2",
        cert_path=d.config.tls_cert_path,
    )
    d.unit_verbatim("external-tls", "systemd/tls-cert-reload.service")


# OpendkimDeployer

DKIM_SELECTOR = "opendkim"

# opendkim-genkey defaults to a weak 1024.
DKIM_KEY_BITS = 2048


def configure_opendkim(d):
    opendkim_config = {
        "domain_name": d.config.mail_domain_bare,
        "opendkim_selector": DKIM_SELECTOR,
    }
    conf_dir = paths.config_dir("opendkim")
    d.j.ensure_directory("opendkim", conf_dir)
    d.put_template(
        "opendkim",
        "config/opendkim/opendkim.conf",
        conf_dir / "opendkim.conf",
        config=opendkim_config,
        trust_anchor_file=str(paths.UNBOUND_ANCHOR),
    )

    d.j.ensure_directory(
        "opendkim", paths.OPENDKIM_ETC, mode=0o750, owner=("opendkim", "opendkim")
    )

    d.j.ensure_directory(
        "opendkim", paths.DKIM_KEYS, mode=0o750, owner=("opendkim", "opendkim")
    )
    for name in ("KeyTable", "SigningTable"):
        d.put_template(
            "opendkim",
            f"config/opendkim/{name}",
            paths.DKIM_KEYS / name,
            config=opendkim_config,
        )
        d.j.chown("opendkim", paths.DKIM_KEYS / name, "opendkim", "opendkim")

    d.j.ensure_directory(
        "opendkim",
        paths.POSTFIX_SPOOL / "opendkim",
        mode=0o750,
        owner=("opendkim", "opendkim"),
    )

    private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
    if not private.exists():
        generate_dkim_key(d)

    d.j.install_dropin(
        "opendkim",
        "opendkim.service",
        "10-prevent-memory-leak.conf",
        d.vendor("systemd/opendkim-10-prevent-memory-leak.conf").read_text(),
    )


def generate_dkim_key(d):
    """openssl rather than opendkim-genkey, which would need Perl."""
    d.out.detail(f"generating a DKIM key for {d.mail_domain}")
    private = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
    d.shell(d.bin("openssl"), "genrsa", "-out", str(private), str(DKIM_KEY_BITS))
    d.j.produced("opendkim", private, f"{DKIM_KEY_BITS}-bit DKIM key")
    d.j.chmod("opendkim", private, 0o600)
    d.j.chown("opendkim", private, "opendkim", "opendkim")
    write_dkim_record(d, private)


def write_dkim_record(d, private: Path):
    """The .txt opendkim-genkey leaves beside the key, for the operator."""
    record = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.txt"
    pubkey = dkim_public_key(d, private)
    d.j.write(
        "opendkim",
        record,
        f'{DKIM_SELECTOR}._domainkey\tIN\tTXT\t( "v=DKIM1; k=rsa; "\n'
        f'\t  "p={pubkey}" )'
        f"  ; ----- DKIM key {DKIM_SELECTOR} for {d.mail_domain}\n",
    )
    d.j.chown("opendkim", record, "opendkim", "opendkim")


def dkim_public_key(d, private: Path) -> str:
    """The base64 body of the public key, the way relay extracts it."""
    result = d.shell(d.bin("openssl"), "rsa", "-in", str(private), "-pubout")
    return "".join(
        line for line in result.stdout.splitlines() if not line.startswith("-")
    )


# DovecotDeployer


def configure_dovecot(d):
    conf_dir = paths.config_dir("dovecot")
    d.j.ensure_directory("dovecot", conf_dir)

    d.put_template(
        "dovecot",
        "config/dovecot/dovecot.conf.j2",
        conf_dir / "dovecot.conf",
        config=d.config,
        debug=False,
        disable_ipv6=d.config.disable_ipv6,
        **d.template_vars(),
    )
    d.put_file("dovecot", "config/dovecot/auth.conf", conf_dir / "auth.conf")
    d.put_file(
        "dovecot",
        "config/dovecot/push_notification.lua",
        conf_dir / "push_notification.lua",
    )

    set_inotify_limits(d)
    ensure_timezone_env(d)

    d.j.install_dropin(
        "dovecot",
        "dovecot.service",
        "10_restart.conf",
        d.vendor("systemd/10_restart_on_failure.conf").read_text(),
    )

    if "dovecot" in d.j:
        d.shell(
            d.bin("dovecot", "doveconf"),
            "-c",
            str(conf_dir / "dovecot.conf"),
            "-n",
        )


def ensure_timezone_env(d):
    """dovecot wants TZ set, and reads it from /etc/environment.

    Appended rather than rewritten, because the host owns this file.
    """
    path = paths.ENVIRONMENT
    line = "TZ=:/etc/localtime"
    existing = path.read_text() if path.is_file() else ""
    if any(entry.strip() == line for entry in existing.splitlines()):
        return
    body = existing if existing.endswith("\n") or not existing else existing + "\n"
    d.j.write("dovecot", path, f"{body}{line}\n")


def set_inotify_limits(d):
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
        if d.j.sysctl(path, "65535\n"):
            wanted = True
            continue
        d.out.warn(
            f"dovecot: sysctl {key}={current}, should be >65534"
            " for production setups (cannot be set here)"
        )
    if wanted:
        d.j.write(
            "dovecot",
            paths.SYSCTL_INOTIFY,
            "fs.inotify.max_user_instances = 65535\n"
            "fs.inotify.max_user_watches = 65535\n",
        )


# PostfixDeployer

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


def configure_postfix(d):
    conf_dir = paths.config_dir("postfix")
    d.j.ensure_directory("postfix", conf_dir)
    tvars = d.template_vars()

    # One write, not a template write followed by an append: two writes
    # of the same file would report a change on every deploy and so
    # defeat every gate that asks whether postfix configuration moved.
    main_cf = render.render_template(
        d.vendor("config/postfix/main.cf.j2"),
        config=d.config,
        disable_ipv6=d.config.disable_ipv6,
        **tvars,
    )
    d.j.write(
        "postfix",
        conf_dir / "main.cf",
        main_cf + store_layout(conf_dir) + postfix_own_settings(conf_dir),
    )
    d.put_template(
        "postfix",
        "config/postfix/master.cf.j2",
        conf_dir / "master.cf",
        config=d.config,
        debug=False,
        **tvars,
    )
    for name in ("submission_header_cleanup", "lmtp_header_cleanup", "login_map"):
        d.put_file("postfix", f"config/postfix/{name}", conf_dir / name)

    write_postfix_store_settings(d, conf_dir)

    # No postmap: postfix reads a regexp table as text at lookup time,
    # and only the indexed types are compiled. test_render.py asserts the
    # declaration is still regexp, so a change to an indexed type fails
    # there rather than silently needing this back.
    d.put_file(
        "postfix",
        "config/postfix/smtp_tls_policy_map",
        conf_dir / "smtp_tls_policy_map",
    )

    ensure_aliases(d)

    # dovecot binds its sockets inside postfix's queue directory and
    # starts first, so the tree has to exist before any service does.
    # 262 ms, hence the gate.
    if "postfix" in d.j or d.spool_created:
        d.shell(d.bin("postfix", "postfix"), "-c", str(conf_dir), "set-permissions")
        d.j.produced("postfix", paths.POSTFIX_SPOOL, "postfix set-permissions")

    d.j.install_dropin(
        "postfix",
        "postfix.service",
        "10_restart.conf",
        d.vendor("systemd/10_restart_on_failure.conf").read_text(),
    )

    if "postfix" in d.j:
        result = d.shell(d.bin("postfix", "postconf"), "-c", str(conf_dir), check=False)
        if result.stderr.strip():
            raise DeployError(f"postfix configuration is invalid:\n{result.stderr}")


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


def write_postfix_store_settings(d, conf_dir: Path):
    """The directories and the group postfix needs before it starts."""
    users.ensure_group("postdrop")
    # set-permissions fixes data/ but not its parent, without which
    # mail_owner cannot traverse in and master(8) dies on its lock file.
    d.j.ensure_directory("postfix", paths.POSTFIX_LIB, mode=0o755)
    d.j.ensure_directory(
        "postfix",
        paths.POSTFIX_LIB / "data",
        mode=0o700,
        owner=("postfix", "root"),
    )
    created = d.j.ensure_directory("postfix", paths.POSTFIX_SPOOL, mode=0o755)
    d.spool_created = created is not None and created.kind == fsops.ADDED
    # postfix looks for postfix-files beside main.cf when it sets
    # permissions; nixpkgs keeps it in the package.
    d.j.symlink(
        "postfix",
        conf_dir / "postfix-files",
        Path(d.bin("postfix", "meta_directory")) / "postfix-files",
    )


def ensure_aliases(d):
    """main.cf points alias_maps at /etc/aliases; a bare host may lack it."""
    aliases = paths.ALIASES
    if not aliases.exists():
        d.j.write("postfix", aliases, "postmaster: root\nroot: root\n")
    db = paths.ALIASES_DB
    if not db.exists() or db.stat().st_mtime < aliases.stat().st_mtime:
        d.shell(
            d.bin("postfix", "postalias"),
            "-c",
            str(paths.config_dir("postfix")),
            f"hash:{paths.ALIASES}",
            check=False,
        )
        d.j.produced("postfix", db, "rebuilt from /etc/aliases")


# NginxDeployer


def configure_nginx(d):
    conf_dir = paths.config_dir("nginx")
    d.j.ensure_directory("nginx", conf_dir)
    tvars = d.template_vars()

    d.put_template(
        "nginx",
        "config/nginx/nginx.conf.j2",
        conf_dir / "nginx.conf",
        config=d.config,
        disable_ipv6=d.config.disable_ipv6,
        **tvars,
    )
    d.put_template(
        "nginx",
        "config/nginx/autoconfig.xml.j2",
        paths.WWW_ROOT / ".well-known/autoconfig/mail/config-v1.1.xml",
        config=d.config,
    )
    d.put_template(
        "nginx",
        "config/nginx/mta-sts.txt.j2",
        paths.WWW_ROOT / ".well-known/mta-sts.txt",
        config=d.config,
    )

    # nginx wants its temp and log directories to exist before it starts.
    for sub in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
        d.j.ensure_directory("nginx", paths.NGINX_LIB / sub)
    d.j.ensure_directory("nginx", paths.NGINX_LOG)
    d.j.ensure_directory("nginx", paths.WWW_ROOT / ".well-known/acme-challenge")

    install_newemail_cgi(d)


def install_newemail_cgi(d):
    """nginx runs chatmaild's newemail.py through fcgiwrap.

    From the installed chatmaild, which travels with its interpreter.
    """
    source = d.find_chatmaild_resource("newemail.py")
    d.j.ensure_directory("nginx", paths.CGI_BIN)
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

    shebang = f"#!{d.bin('python')}\n"
    d.j.write("nginx", target, shebang + body, mode=0o755)


# MtailDeployer


def configure_mtail(d):
    """Nothing at all unless mtail is wanted; the binary is the operator's."""
    if not d.config.mtail_address:
        return

    progs_dir = paths.config_dir("mtail")
    d.j.ensure_directory("mtail", progs_dir)
    d.unit_from_template(
        "mtail",
        "systemd/mtail.service.j2",
        address=d.config.mtail_address,
        port=3903,
        bin_path=str(paths.MTAIL),
        progs_dir=str(progs_dir),
    )

    for prog in sorted((d.bundle.vendor / "config/mtail").glob("*.mtail")):
        d.j.copy("mtail", prog, progs_dir / prog.name)
    # In the closure by the same hash relay downloads it under.
    d.j.copy(
        "mtail",
        Path(d.bundle.resolve(d.bin("filtermail", "mtail_program"))),
        progs_dir / "filtermail.mtail",
    )

    if "mtail" in d.j:
        validate_mtail_programs(d, progs_dir)


def validate_mtail_programs(d, progs_dir: Path):
    """Fail the deploy rather than leave mtail crash-looping."""
    d.shell(
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
