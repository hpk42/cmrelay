"""chatmail.ini, read by chatmaild's `Config`."""

from pathlib import Path

from chatmaild.config import read_config as _read_config

from .. import CmrelayError, paths
from .website.website import DISABLED


def read_config(inipath: Path):
    if not inipath.is_file():
        raise CmrelayError(
            f"this host has no {inipath} yet; create it first:"
            "\n\n      cmrelay init chat.example.org"
        )
    try:
        return _read_config(inipath)
    except KeyError as exc:
        raise CmrelayError(
            f"{inipath} is missing {exc.args[0]!r};"
            " a chatmail.ini needs a [params] section with mail_domain in it"
        ) from None
    except ValueError as exc:
        raise CmrelayError(f"{inipath}: {exc}") from None


def warnings(config, inipath: Path) -> list:
    """Everything worth saying about this ini before anything is written."""
    found = []
    # ssh_host is cmdeploy's, and an adopted chatmail.ini has it
    unused = set(config._unused_keys) - {"ssh_host"}
    if unused:
        found.append(
            f"{inipath} sets {', '.join(sorted(unused))}, which nothing reads."
            " Check the spelling, or remove them."
        )
    folder = config.www_folder.strip()
    if folder and folder != DISABLED:
        found.append(
            f"www_folder = {folder} is ignored: the web pages come from"
            f" {paths.WWW_SRC}. Remove the setting, or set it to '{DISABLED}'"
            " to publish no pages."
        )
    if config.tls_cert_mode == "acme" and not (config.acme_email or "").strip():
        found.append(
            "acme_email is empty, so acmetool registers an anonymous account"
            " and Let's Encrypt has no way to warn about an expiring"
            " certificate."
        )
    return found
