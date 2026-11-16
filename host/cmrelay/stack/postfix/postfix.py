"""postfix: SMTP in and out, with its queue under /var/spool/postfix."""

from pathlib import Path

from ... import CmrelayError, paths

CONF_DIR = paths.CONFIG / "postfix"
MAILNAME = Path("/etc/mailname")
SPOOL = Path("/var/spool/postfix")
LIB = Path("/var/lib/postfix")
# the host's CA bundle; a store copy would freeze it at build time
CA_CERTS = Path("/etc/ssl/certs")


def configure(deploy):
    # Used by the auth proxy. https://wiki.debian.org/EtcMailName
    deploy.changes.write("postfix", MAILNAME, f"{deploy.config.mail_domain}\n")
    data = dict(config=deploy.config, config_dir=str(CONF_DIR), ca_path=str(CA_CERTS))
    source = "stack/postfix"
    # one write, or every deploy would report a change
    main_cf = deploy.render(
        f"{source}/main.cf.j2", disable_ipv6=deploy.config.disable_ipv6, **data
    )
    layout = store_layout(CONF_DIR, deploy.bin("postfix", "path"))
    deploy.changes.write("postfix", CONF_DIR / "main.cf", main_cf + layout)
    deploy.put_template(
        "postfix", f"{source}/master.cf.j2", CONF_DIR / "master.cf", debug=False, **data
    )
    # no postmap for smtp_tls_policy_map: a regexp table is read as text
    for name in (
        "submission_header_cleanup",
        "lmtp_header_cleanup",
        "login_map",
        "smtp_tls_policy_map",
    ):
        deploy.put_file("postfix", f"{source}/{name}", CONF_DIR / name)

    # set-permissions fixes data/ but not its parent, which mail_owner must traverse.
    deploy.changes.ensure_directory("postfix", LIB)
    data = LIB / "data"
    owner = ("postfix", "root")
    deploy.changes.ensure_directory("postfix", data, mode=0o700, owner=owner)
    deploy.changes.ensure_directory("postfix", SPOOL)
    # nixpkgs keeps postfix-files in the package, postfix wants it beside main.cf
    files = Path(deploy.bin("postfix", "path")) / "etc/postfix/postfix-files"
    deploy.changes.symlink("postfix", CONF_DIR / "postfix-files", files)

    # dovecot binds sockets in postfix's queue directory and starts first
    if "postfix" in deploy.changes:
        deploy.bundle.run("postfix", "-c", CONF_DIR, "set-permissions")
        deploy.changes.note(SPOOL, "postfix")
        result = deploy.bundle.run("postconf", "-c", CONF_DIR, check=False)
        if result.stderr.strip():
            raise CmrelayError(f"postfix configuration is invalid:\n{result.stderr}")

    deploy.put_format_unit(
        "postfix",
        f"{source}/postfix.service",
        postfix_bin=deploy.bin("postfix", "postfix"),
        postfix_config_dir=str(CONF_DIR),
    )


def store_layout(conf_dir: Path, postfix: str) -> str:
    """The directory settings a store-installed postfix cannot infer."""
    # every line `postfix set-permissions` would append, so an unchanged deploy restarts nothing
    settings = {
        "config_directory": conf_dir,
        "queue_directory": SPOOL,
        "data_directory": LIB / "data",
        "command_directory": f"{postfix}/bin",
        "daemon_directory": f"{postfix}/libexec/postfix",
        "html_directory": f"{postfix}/share/postfix/doc/html",
        "mail_owner": "postfix",
        "mailq_path": f"{postfix}/bin/mailq",
        "manpage_directory": f"{postfix}/share/man",
        "meta_directory": f"{postfix}/etc/postfix",
        "newaliases_path": f"{postfix}/bin/newaliases",
        "sample_directory": "/etc/postfix",
        "sendmail_path": f"{postfix}/bin/sendmail",
        "setgid_group": "postdrop",
        "shlib_directory": "no",
    }
    body = "\n".join(f"{k} = {v}" for k, v in settings.items())
    return f"\n# --- written by cmrelay: store layout ---\n{body}\n"


def units(deploy) -> list:
    return [("postfix.service", not deploy.disable_mail, "postfix")]
