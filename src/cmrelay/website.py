"""Build the relay's web pages, the way cmdeploy's www.py builds them.

Reimplemented rather than reused because www.py is cmdeploy's code, which is
what the vendoring model exists to avoid carrying. Same tree from the same
sources.

Nothing here writes a file: the pages come back as bytes and the deployment
publishes them through the journal.
"""

import io
import re
from pathlib import Path

import markdown
import qrcode
import qrcode.image.svg
from jinja2 import Template

from .errors import CmrelayError

LAYOUT = "page-layout.html"

# A page carrying these was never meant to be published.
_MERGE_CONFLICT = re.compile(r"^<<<<<<<.+^=======.+^>>>>>>>", re.DOTALL | re.MULTILINE)


class WebsiteError(CmrelayError):
    pass


def int_to_english(number: int) -> str:
    words = (
        "zero one two three four five six seven eight nine ten eleven twelve"
    ).split()
    if 0 <= number <= 12:
        return words[number]
    if number <= 50:
        return str(number)
    return "more"


def gen_qr_svg(mail_domain: str) -> str:
    """The DCACCOUNT invite code, as an SVG.

    relay composites this into a PNG with Pillow, which costs 74 MiB of image
    codecs in the closure for one picture per host. The SVG carries the same
    code and needs nothing beyond qrcode.
    """
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4
    )
    qr.add_data(f"DCACCOUNT:https://{mail_domain}/new")
    qr.make(fit=True)
    out = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(out)
    return out.getvalue().decode()


def find_merge_conflict(src_dir: Path) -> Path | None:
    for path in sorted(Path(src_dir).iterdir()):
        if path.suffix not in (".css", ".html", ".md") or not path.is_file():
            continue
        if _MERGE_CONFLICT.search(path.read_text(errors="replace")):
            return path
    return None


def render_page(source: Path, layout: str, config, extra: dict) -> str:
    render_vars = {
        "pagename": "home" if source.stem == "index" else source.stem,
        "markdown_html": markdown.markdown(source.read_text()),
        **extra,
    }
    content = layout
    # Jinja inside the markdown only becomes visible once the layout has been
    # expanded, so render until the output stops changing.
    while True:
        new = Template(content).render(config=config, **render_vars)
        if new == content:
            return new
        content = new


def render_site(src_dir: Path, config) -> dict:
    """Render src_dir into {filename: bytes}, without touching the filesystem."""
    src_dir = Path(src_dir)
    conflict = find_merge_conflict(src_dir)
    if conflict is not None:
        raise WebsiteError(
            f"{conflict} still carries merge conflict markers; refusing to publish it"
        )

    layout_path = src_dir / LAYOUT
    if not layout_path.is_file():
        raise WebsiteError(f"{layout_path} is missing; cannot render pages")
    layout = layout_path.read_text()

    mail_domain = config.mail_domain
    pages = {f"qr-chatmail-invite-{mail_domain}.svg": gen_qr_svg(mail_domain).encode()}

    extra = {
        "username_min_length": int_to_english(config.username_min_length),
        "username_max_length": int_to_english(config.username_max_length),
        "password_min_length": int_to_english(config.password_min_length),
    }

    for path in sorted(src_dir.iterdir()):
        if path.suffix == ".md":
            pages[f"{path.stem}.html"] = render_page(
                path, layout, config, extra
            ).encode()
        elif path.name != LAYOUT and path.is_file():
            pages[path.name] = path.read_bytes()
    return pages


def copy_tree(src_dir: Path) -> dict:
    """A directory published as it stands, for a www_folder with no index.md."""
    files = {}
    for path in sorted(Path(src_dir).rglob("*")):
        if path.is_file():
            files[str(path.relative_to(src_dir))] = path.read_bytes()
    return files
