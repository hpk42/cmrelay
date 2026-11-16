"""chatmaild's daemons and timers: accounts, metadata, expiry."""

from pathlib import Path

from ... import paths

# The timers own expire and fsreport.
UNITS = [
    "doveauth.service",
    "chatmail-metadata.service",
    "lastlogin.service",
    "chatmail-expire.service",
    "chatmail-expire.timer",
    "chatmail-fsreport.service",
    "chatmail-fsreport.timer",
]
TIMER_DRIVEN = {"chatmail-expire.service", "chatmail-fsreport.service"}


def configure(deploy):
    # chatmail-metadata crashes if the mailboxes dir does not exist
    mailboxes = Path(deploy.config.mailboxes_dir)
    owner = ("vmail", "vmail")
    deploy.changes.ensure_directory("chatmaild", mailboxes, mode=0o700, owner=owner)
    for unit in UNITS:
        deploy.put_format_unit(
            "chatmaild",
            f"stack/chatmaild/{unit}.f",
            execpath=deploy.bin("python", unit.split(".")[0]),
            config_path=str(paths.INI),
        )


def units(deploy) -> list:
    on = [
        (u, True, "chatmaild", "chatmail.ini") for u in UNITS if u not in TIMER_DRIVEN
    ]
    # left as they are: oneshots their timers start
    return on + [(u, None) for u in sorted(TIMER_DRIVEN)]
