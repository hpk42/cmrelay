"""journald: logs in memory only, for three days."""

from ... import paths


def configure(deploy):
    name = "90-volatile-logging.conf"
    text = deploy.changes.marked(deploy.source(f"stack/journald/{name}").read_text())
    deploy.changes.write("journald", paths.JOURNALD_DROPINS / name, text)


def units(deploy) -> list:
    # restarted, never started or stopped: the host's own
    return [("systemd-journald.service", None, "journald")]
