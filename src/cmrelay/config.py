"""chatmail.ini, read and checked by relay's own code.

`Config` is chatmaild's, so every setting means what it means to a cmdeploy
host. What this adds is the checks cmdeploy makes around it: a malformed ini,
settings with no effect, foreign privacy contacts, and an empty acme_email.
"""

import tempfile
from pathlib import Path

# Deliberately not a PyPI dependency: relay's own code, built from the
# vendored commit into the same interpreter as the rest of the closure.
from chatmaild.config import read_config as _read_config
from chatmaild.config import write_initial_config as _write_initial_config

from . import paths

# relay refuses these for a domain it is not itself responsible for, so that
# a relay does not publish somebody else's privacy contact on its own page.
BLOCKED_PRIVACY_WORDS = ("merlinux", "schmieder", "testrun.org")


class ConfigError(Exception):
    """The ini cannot be read, and the message says where to fix it."""


def read_config(inipath: Path = paths.INI):
    """relay's own Config, with a readable refusal instead of a traceback."""
    inipath = Path(inipath)
    try:
        return _read_config(inipath)
    except AssertionError:
        raise ConfigError(f"no configuration file at {inipath}") from None
    except KeyError as exc:
        raise ConfigError(
            f"{inipath} is missing {exc.args[0]!r};"
            " a chatmail.ini needs a [params] section with mail_domain in it"
        ) from None
    except ValueError as exc:
        raise ConfigError(f"{inipath}: {exc}") from None


def write_initial_config(inipath: Path, mail_domain: str, overrides=None):
    """The same function `cmdeploy init` calls, so the ini is relay's, not ours."""
    _write_initial_config(Path(inipath), mail_domain, overrides or {})


def default_config(mail_domain: str, overrides=None):
    """A Config from relay's own defaults, for a host that has no ini yet."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chatmail.ini"
        _write_initial_config(path, mail_domain, overrides or {})
        return _read_config(path)


def parse_overrides(pairs) -> dict:
    overrides = {}
    for item in pairs or []:
        if "=" not in item:
            raise ConfigError(f"--set expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()
    return overrides


def unused_settings(config) -> list:
    """Settings chatmaild parsed and no component reads."""
    return list(getattr(config, "_unused_keys", []) or [])


def foreign_privacy_contacts(config) -> list:
    """Privacy settings still naming relay's own operators.

    Reimplemented because relay keeps this in deployers.py, which imports
    pyinfra; its right home is chatmaild.config. A warning rather than
    relay's hard error, which is for deployments relay is responsible for.
    """
    domain = config.mail_domain
    if domain == "testrun.org" or domain.endswith(".testrun.org"):
        return []
    return [
        key
        for key, value in vars(config).items()
        if key.startswith("privacy")
        and any(word in str(value) for word in BLOCKED_PRIVACY_WORDS)
    ]


def warnings(config, inipath: Path = paths.INI) -> list:
    """Everything worth saying about this ini before anything is written."""
    found = []
    unused = unused_settings(config)
    if unused:
        found.append(
            f"{inipath} sets {', '.join(sorted(unused))}, which nothing reads."
            " Check the spelling, or remove them."
        )
    foreign = foreign_privacy_contacts(config)
    if foreign:
        found.append(
            f"{inipath} still names relay's own operators in"
            f" {', '.join(sorted(foreign))}."
            " Set your own privacy contacts before publishing this relay."
        )
    if config.tls_cert_mode == "acme" and not (config.acme_email or "").strip():
        found.append(
            "acme_email is empty, so acmetool registers an anonymous account"
            " and Let's Encrypt has no way to warn about an expiring"
            " certificate."
        )
    return found
