#!/usr/bin/env python3
"""Write a cmrelay wheel with the payload inside it.

`pip install cmrelay` should be the last thing that needs the network. The
alternative -- a small wheel that downloads its payload on first use -- means
the host installing the relay must reach GitHub, which is exactly the
constraint an operator staging an air-gapped or firewalled machine does not
have. So the payload travels in the wheel and platform tags carry the arch.

This follows deltachat-rpc-server, which packages its Rust binary the same
way: no build backend, the wheel written directly as a zip, and a console
script that hands over to the binary beside it. Two things there are worth
not repeating. Its WHEEL metadata says `Tag: {tag}` in every wheel ever
published, because the line is a plain string where an f-string was meant;
and between releases its version carries `-dev`, which pip rejects in a
filename because a build tag has to start with a digit.

Written against the standard library alone -- no `wheel`, no `setuptools` --
so CI can run it straight after `nix build` with whatever python3 is present.
"""

import argparse
import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

# No musllinux. cmrelay refuses Alpine, twice over: the closure is glibc and
# the units need systemd, not OpenRC. Claiming the tag would advertise hosts
# where the installer declines to run.
#
# manylinux_2_17 is a floor the payload does not actually need -- the closure
# carries its own glibc, and the installer wants a POSIX shell and tar -- but
# it is the oldest tag current pip and PyPI both accept without argument.
ARCH_TAGS = {
    "x86_64": ["manylinux_2_17_x86_64", "manylinux2014_x86_64"],
    "aarch64": ["manylinux_2_17_aarch64", "manylinux2014_aarch64"],
}

# Where the CLI looks for them. Shared with src/cmrelay/payload.py and,
# for the signature, src/cmrelay/discovery.py: `cmrelay upgrade` can take a
# candidate from PyPI, and a wheel with no signature in it is one nothing can
# verify against the release keys.
PAYLOAD_MEMBER = "cmrelay/_payload/cmrelay.run"
SIGNATURE_MEMBER = "cmrelay/_payload/cmrelay.run.sig"

# A fixed timestamp, so two builds of the same input give the same zip.
EPOCH = (1980, 1, 1, 0, 0, 0)


def metadata(version: str, readme: str) -> str:
    """The wheel's METADATA.

    Deliberately no dependencies. A wheel-installed cmrelay reaches only its
    wheel-side modules -- cli, output, version, bundle and payload -- before
    it hands over, and jinja2, markdown and qrcode are all used
    on the far side of that handover, by the closure's own interpreter.
    Declaring them here would make `pip install cmrelay` drag three packages
    that nothing in the wheel imports.
    """
    fields = [
        ("Metadata-Version", "2.1"),
        ("Name", "cmrelay"),
        ("Version", version),
        (
            "Summary",
            "Single-file installer for a chatmail relay, built as a Nix closure",
        ),
        ("License", "GPL-3.0-or-later"),
        ("Requires-Python", ">=3.9"),
        ("Project-URL", "Repository, https://github.com/chatmail/cmrelay"),
        (
            "Classifier",
            "License :: OSI Approved :: GNU General Public License v3 or later"
            " (GPLv3+)",
        ),
        ("Classifier", "Operating System :: POSIX :: Linux"),
        ("Classifier", "Topic :: Communications :: Email"),
        ("Description-Content-Type", "text/markdown"),
    ]
    head = "".join(f"{k}: {v}\n" for k, v in fields)
    return f"{head}\n{readme}"


def wheel_metadata(tags: list[str]) -> str:
    """The WHEEL file.

    One `Tag:` line per platform. The filename compresses them into a single
    dotted field; this file does not, and writing the compressed form here
    would be a spec violation rather than a shortcut.
    """
    lines = [
        "Wheel-Version: 1.0",
        "Generator: wheel-cmrelay.py",
        "Root-Is-Purelib: false",
    ]
    lines += [f"Tag: {tag}" for tag in tags]
    return "\n".join(lines) + "\n"


def check_version(version: str) -> str:
    """Reject a version pip would refuse to install from a filename.

    A wheel filename splits on `-`, so a version containing one turns into a
    build tag, and a build tag must start with a digit. `2.60.0-dev` is the
    shape that bites: it never appears in a release, only in a manual CI run,
    and it yields artifacts nobody can install.
    """
    if "-" in version or " " in version:
        raise SystemExit(
            f"cmrelay: {version!r} cannot go in a wheel filename: a '-' makes"
            " what follows a build tag, which must start with a digit."
            " Use '.dev0' or '+local' rather than '-dev'."
        )
    return version


def add(zf: zipfile.ZipFile, record: list, name: str, data: bytes, mode: int):
    info = zipfile.ZipInfo(name, date_time=EPOCH)
    info.external_attr = (mode << 16) | 0o100000
    # The payload is already zstd-compressed. Deflating it again spends
    # minutes to gain nothing, so it goes in stored.
    info.compress_type = (
        zipfile.ZIP_STORED if name == PAYLOAD_MEMBER else zipfile.ZIP_DEFLATED
    )
    zf.writestr(info, data)
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    record.append((name, f"sha256={digest.decode()}", len(data)))


def build(
    payload: Path,
    arch: str,
    version: str,
    src: Path,
    root: Path,
    outdir: Path,
    signature: Path | None = None,
):
    tags = [f"py3-none-{t}" for t in ARCH_TAGS[arch]]
    filename = f"cmrelay-{version}-py3-none-{'.'.join(ARCH_TAGS[arch])}.whl"
    dist_info = f"cmrelay-{version}.dist-info"

    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / filename
    record: list = []

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((src / "cmrelay").rglob("*.py")):
            member = str(path.relative_to(src))
            add(zf, record, member, path.read_bytes(), 0o644)

        # 0o755 in the zip, because pip restores the mode from it and the CLI
        # execs this file directly. Getting it wrong yields an installed
        # wheel whose payload cannot be run.
        add(zf, record, PAYLOAD_MEMBER, payload.read_bytes(), 0o755)
        if signature is not None:
            add(zf, record, SIGNATURE_MEMBER, signature.read_bytes(), 0o644)

        readme = (root / "README.md").read_text(encoding="utf-8")
        add(
            zf,
            record,
            f"{dist_info}/METADATA",
            metadata(version, readme).encode(),
            0o644,
        )
        add(zf, record, f"{dist_info}/WHEEL", wheel_metadata(tags).encode(), 0o644)
        add(
            zf,
            record,
            f"{dist_info}/entry_points.txt",
            b"[console_scripts]\ncmrelay = cmrelay.cli:main\n",
            0o644,
        )
        # The payload travels in this wheel, so the terms of everything
        # inside it travel with it.
        for name in ("LICENSE", "THIRD-PARTY.md"):
            add(
                zf,
                record,
                f"{dist_info}/licenses/{name}",
                (root / name).read_bytes(),
                0o644,
            )

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerows(record)
        # RECORD cannot hash itself.
        writer.writerow([f"{dist_info}/RECORD", "", ""])
        info = zipfile.ZipInfo(f"{dist_info}/RECORD", date_time=EPOCH)
        info.external_attr = (0o644 << 16) | 0o100000
        zf.writestr(info, buf.getvalue())

    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--payload", required=True, type=Path, help="the .run file to embed"
    )
    parser.add_argument("--arch", required=True, choices=sorted(ARCH_TAGS))
    parser.add_argument(
        "--version", required=True, help="PEP 440 version, without a '-'"
    )
    parser.add_argument(
        "--signature",
        type=Path,
        help="the .sig to carry beside the payload (default: <payload>.sig)",
    )
    parser.add_argument("--outdir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent.parent,
        help="the cmrelay checkout, for README and LICENSE",
    )
    args = parser.parse_args(argv)

    version = check_version(args.version)
    if not args.payload.is_file():
        raise SystemExit(f"cmrelay: no payload at {args.payload}")

    signature = args.signature or Path(f"{args.payload}.sig")
    if not signature.is_file():
        if args.signature is not None:
            raise SystemExit(f"cmrelay: no signature at {signature}")
        signature = None

    target = build(
        args.payload,
        args.arch,
        version,
        args.root / "src",
        args.root,
        args.outdir,
        signature=signature,
    )
    size = target.stat().st_size / (1024 * 1024)
    print(f"{target}  ({size:.1f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
