"""iroh-relay, for realtime webxdc traffic."""

from ... import paths

CONF = paths.CONFIG / "iroh/iroh-relay.toml"


def configure(deploy):
    deploy.put_file("iroh", "stack/iroh/iroh-relay.toml", CONF)
    deploy.put_unit(
        "iroh",
        "stack/iroh/iroh-relay.service.j2",
        bin_path=deploy.bin("iroh-relay"),
        config_path=str(CONF),
    )


def units(deploy) -> list:
    return [("iroh-relay.service", bool(deploy.config.enable_iroh_relay), "iroh")]
