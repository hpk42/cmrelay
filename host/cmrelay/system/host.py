"""What kind of host this is."""

from dataclasses import dataclass

from .. import paths


@dataclass
class Host:
    state: str
    relay_commit: str | None = None

    BARE = "bare"
    INSTALLED = "installed"
    CMDEPLOY = "cmdeploy"

    @classmethod
    def classify(cls) -> "Host":
        """One detector pass, no writes. Only a deploy makes a host `installed`."""
        marker = paths.VERSION_MARKER
        words = marker.read_text().split() if marker.is_file() else []
        commit = next(iter(words), None)
        if paths.INI_DIGEST.is_file():
            return cls(cls.INSTALLED, commit)
        if paths.CMDEPLOY_INI.is_file() or commit:
            return cls(cls.CMDEPLOY, commit)
        return cls(cls.BARE)

    @property
    def takes_over(self) -> bool:
        return self.state == self.CMDEPLOY

    @property
    def description(self) -> str:
        return {
            self.BARE: "no chatmail relay on it yet",
            self.INSTALLED: "installed by cmrelay",
            self.CMDEPLOY: "running chatmail, deployed by cmdeploy",
        }[self.state]
