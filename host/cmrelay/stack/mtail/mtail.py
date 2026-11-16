"""mtail, only when chatmail.ini asks for it; the binary is the operator's."""

from ... import paths

PROGS_DIR = paths.CONFIG / "mtail"


def configure(deploy):
    if not deploy.config.mtail_address:
        return
    deploy.put_unit(
        "mtail",
        "stack/mtail/mtail.service.j2",
        address=deploy.config.mtail_address,
        port=3903,
        bin_path=str(paths.MTAIL),
        progs_dir=str(PROGS_DIR),
    )
    name = "delivered_mail.mtail"
    deploy.put_file("mtail", f"stack/mtail/{name}", PROGS_DIR / name)
    filtermail_prog = deploy.bin("filtermail", "mtail_program")
    deploy.changes.copy("mtail", filtermail_prog, PROGS_DIR / "filtermail.mtail")


def units(deploy) -> list:
    return [("mtail.service", bool(deploy.config.mtail_address), "mtail")]
