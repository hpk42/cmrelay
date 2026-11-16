"""chatmail-turn, the TURN server for calls."""


def configure(deploy):
    deploy.put_format_unit(
        "turnserver",
        "stack/turn/turnserver.service.f",
        bin_path=deploy.bin("chatmail-turn"),
        mail_domain=deploy.config.mail_domain_bare,
    )


def units(deploy) -> list:
    return [("turnserver.service", True, "turnserver", "chatmail.ini")]
