"""dovecot: IMAP and LMTP, and the kernel limits it asks for."""

from pathlib import Path

from ... import paths

CONF_DIR = paths.CONFIG / "dovecot"
CONF = CONF_DIR / "dovecot.conf"
ENVIRONMENT = Path("/etc/environment")
PROC_INOTIFY = Path("/proc/sys/fs/inotify")


def configure(deploy):
    source = "stack/dovecot"
    deploy.put_template(
        "dovecot",
        f"{source}/dovecot.conf.j2",
        CONF,
        config=deploy.config,
        debug=False,
        disable_ipv6=deploy.config.disable_ipv6,
        config_dir=str(CONF_DIR),
        dh_path=deploy.bin("dovecot", "dh_params"),
        quota_expire_bin=deploy.bin("python", "chatmail-quota-expire"),
    )
    deploy.put_template(
        "dovecot", f"{source}/auth.lua.j2", CONF_DIR / "auth.lua", config=deploy.config
    )
    name = "push_notification.lua"
    deploy.put_file("dovecot", f"{source}/{name}", CONF_DIR / name)

    set_inotify_limits(deploy)
    # dovecot reads TZ from /etc/environment; append, the host owns the file
    line = "TZ=:/etc/localtime"
    existing = ENVIRONMENT.read_text() if ENVIRONMENT.is_file() else ""
    if line not in map(str.strip, existing.splitlines()):
        body = existing + "\n" if existing and not existing.endswith("\n") else existing
        deploy.changes.write("dovecot", ENVIRONMENT, f"{body}{line}\n")

    if "dovecot" in deploy.changes:
        deploy.bundle.run("doveconf", "-c", CONF, "-n")

    deploy.put_format_unit(
        "dovecot",
        f"{source}/dovecot.service",
        dovecot_bin=deploy.bin("dovecot"),
        doveadm_bin=deploy.bin("dovecot", "doveadm"),
        dovecot_config=str(CONF),
    )


def set_inotify_limits(deploy):
    """Recommended by dovecot; not settable inside an unprivileged container."""
    wanted = False
    for name in ("max_user_instances", "max_user_watches"):
        path = PROC_INOTIFY / name
        try:
            current = int(path.read_text())
        except (OSError, ValueError):
            continue
        if current > 65534:
            continue
        try:
            path.write_text("65535\n")
            wanted = True
        except OSError:
            deploy.out.warn(
                f"dovecot: sysctl fs.inotify.{name}={current}, should be >65534"
                " for production setups (cannot be set here)"
            )
    if wanted:
        deploy.changes.write(
            "dovecot",
            paths.SYSCTL_INOTIFY,
            deploy.changes.marked(
                "fs.inotify.max_user_instances = 65535\n"
                "fs.inotify.max_user_watches = 65535\n"
            ),
        )


def units(deploy) -> list:
    return [("dovecot.service", not deploy.disable_mail, "dovecot")]
