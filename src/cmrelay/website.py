"""Build the relay's web pages, the way cmdeploy's www.py builds them.

This is one of the few places cmrelay reimplements rather than reuses, for
the same reason it reimplements the deployers: www.py is cmdeploy's code, and
vendoring cmdeploy's Python is what the vendoring model exists to avoid.
What it produces is the same tree from the same sources -- the markdown pages
under vendor/www rendered through page-layout.html, plus the invite QR code.

The QR code is the one deliberate difference, and it is an SVG rather than
relay's composited PNG. Where relay's web pages end up is an open question,
so cmrelay should not carry 74 MiB of image codecs on their behalf while it
is undecided.

Rendering is recursive because relay's pages contain jinja expressions inside
the markdown, which land inside the layout and have to be expanded again.
"""

import io
from pathlib import Path

import markdown
import qrcode
import qrcode.image.svg
from jinja2 import Template

QR_BORDER = 4
QR_BOX_SIZE = 10


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

    relay draws this as a PNG with the Delta Chat logo composited into the
    middle and the domain written underneath, which needs Pillow. Pillow
    costs 74 MiB of image codecs in the closure, almost all of it formats a
    relay never touches, to produce one picture per host. An SVG carries the
    same code, scales better, and needs nothing beyond qrcode itself.
    """
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(f"DCACCOUNT:https://{mail_domain}/new")
    qr.make(fit=True)
    out = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(out)
    return out.getvalue().decode()


def render_page(source: Path, layout: str, config, extra: dict) -> str:
    render_vars = {
        "pagename": "home" if source.stem == "index" else source.stem,
        "markdown_html": markdown.markdown(source.read_text()),
        **extra,
    }
    content = layout
    # relay's pages carry jinja inside the markdown, which only becomes
    # visible to the template engine once the layout has been expanded, so
    # render until the output stops changing.
    while True:
        new = Template(content).render(config=config, **render_vars)
        if new == content:
            return new
        content = new


def build_website(src_dir: Path, config, build_dir: Path | None = None) -> Path:
    """Render src_dir into build_dir, returning it."""
    src_dir = Path(src_dir)
    if build_dir is None:
        build_dir = Path("/opt/cmrelay/state/www-build")
    build_dir.mkdir(parents=True, exist_ok=True)

    layout_path = src_dir / "page-layout.html"
    if not layout_path.is_file():
        raise FileNotFoundError(f"{layout_path} is missing; cannot render pages")
    layout = layout_path.read_text()

    mail_domain = config.mail_domain
    (build_dir / f"qr-chatmail-invite-{mail_domain}.svg").write_text(
        gen_qr_svg(mail_domain)
    )

    extra = {
        "username_min_length": int_to_english(config.username_min_length),
        "username_max_length": int_to_english(config.username_max_length),
        "password_min_length": int_to_english(config.password_min_length),
    }

    for path in sorted(src_dir.iterdir()):
        if path.suffix == ".md":
            html = render_page(path, layout, config, extra)
            (build_dir / f"{path.stem}.html").write_text(html)
        elif path.name != "page-layout.html" and path.is_file():
            (build_dir / path.name).write_bytes(path.read_bytes())

    return build_dir
