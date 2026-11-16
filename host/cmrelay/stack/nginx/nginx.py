"""nginx, and fcgiwrap behind it for account creation."""

import importlib.resources
from pathlib import Path

from ... import CmrelayError, paths

CONF = paths.CONFIG / "nginx/nginx.conf"
CGI_BIN = paths.LIB / "cgi-bin"
LIB = Path("/var/lib/nginx")
LOG = Path("/var/log/nginx")


def configure(deploy):
    source, config = "stack/nginx", deploy.config
    prefix = deploy.bin("nginx", "prefix")
    deploy.put_template(
        "nginx",
        f"{source}/nginx.conf.j2",
        CONF,
        config=config,
        disable_ipv6=config.disable_ipv6,
        config_dir=f"{prefix}/conf",
        stream_module="",
        www_root=str(paths.WWW_ROOT),
        cgi_dir=str(CGI_BIN),
    )
    known = paths.WWW_ROOT / ".well-known"
    autoconfig = known / "autoconfig/mail/config-v1.1.xml"
    deploy.put_template(
        "nginx", f"{source}/autoconfig.xml.j2", autoconfig, config=config
    )
    mta_sts = known / "mta-sts.txt"
    deploy.put_template("nginx", f"{source}/mta-sts.txt.j2", mta_sts, config=config)
    for sub in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
        deploy.changes.ensure_directory("nginx", LIB / sub)
    deploy.changes.ensure_directory("nginx", LOG)
    deploy.changes.ensure_directory("nginx", known / "acme-challenge")

    # nginx runs chatmaild's newemail.py through fcgiwrap, with our interpreter and ini
    body = importlib.resources.files("chatmaild").joinpath("newemail.py").read_text()
    if body.startswith("#!"):
        body = body.split("\n", 1)[1]
    old_config = 'CONFIG_PATH = "/usr/local/lib/chatmaild/chatmail.ini"'
    if old_config not in body:
        raise CmrelayError(
            "chatmaild's newemail.py no longer sets CONFIG_PATH the way"
            " cmrelay rewrites it; account creation would read the wrong"
            " configuration"
        )
    body = body.replace(old_config, f'CONFIG_PATH = "{paths.INI}"')
    cgi = CGI_BIN / "newemail.py"
    deploy.changes.write("nginx", cgi, f"#!{deploy.bin('python')}\n{body}", mode=0o755)

    names = dict(nginx_bin=deploy.bin("nginx"), nginx_prefix=prefix)
    names.update(nginx_config=str(CONF), fcgiwrap_bin=deploy.bin("fcgiwrap"))
    for name in ("fcgiwrap.service", "fcgiwrap.socket", "nginx.service"):
        deploy.put_format_unit(name.split(".")[0], f"{source}/{name}", **names)


def units(deploy) -> list:
    return [
        ("fcgiwrap.socket", True, "fcgiwrap"),
        ("fcgiwrap.service", True, "fcgiwrap"),
        ("nginx.service", True, "nginx"),
    ]
