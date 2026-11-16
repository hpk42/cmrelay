"""filtermail: processes incoming/outgoing messages."""

from ... import paths

UNITS = ["filtermail", "filtermail-incoming", "filtermail-transport"]


def configure(deploy):
    for name in UNITS:
        deploy.put_unit(
            "filtermail",
            f"stack/filtermail/{name}.service.j2",
            bin_path=deploy.bin("filtermail"),
            config_path=str(paths.INI),
        )


def units(deploy) -> list:
    return [(f"{u}.service", True, "filtermail", "chatmail.ini") for u in UNITS]
