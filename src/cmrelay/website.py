"""Build the relay's web pages, the way cmdeploy's www.py builds them.

This is one of the few places cmrelay reimplements rather than reuses, for
the same reason it reimplements the deployers: www.py is cmdeploy's code, and
vendoring cmdeploy's Python is what the vendoring model exists to avoid.
What it produces is the same tree from the same sources -- the markdown pages
under vendor/www rendered through page-layout.html, plus the invite QR code.

Rendering is recursive because relay's pages contain jinja expressions inside
the markdown, which land inside the layout and have to be expanded again.
"""

import io
from pathlib import Path

import markdown
import qrcode
from jinja2 import Template
from PIL import Image, ImageDraw, ImageFont

QR_BORDER = 4
FONT_SIZE = 22


def int_to_english(number: int) -> str:
    words = (
        "zero one two three four five six seven eight nine ten eleven twelve"
    ).split()
    if 0 <= number <= 12:
        return words[number]
    if number <= 50:
        return str(number)
    return "more"


def gen_qr_png(mail_domain: str, bundle) -> bytes:
    """The DCACCOUNT invite code, drawn over relay's own artwork."""
    url = f"DCACCOUNT:https://{mail_domain}/new"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=QR_BORDER,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    logo_path = bundle.vendor / "data" / "delta-chat-bw.png"
    if logo_path.is_file():
        logo = Image.open(logo_path).convert("RGBA")
        side = image.size[0] // 5
        logo = logo.resize((side, side))
        offset = ((image.size[0] - side) // 2, (image.size[1] - side) // 2)
        image.paste(logo, offset, logo)

    font_path = bundle.vendor / "data" / "opensans-regular.ttf"
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(str(font_path), FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()
    text = mail_domain
    width = draw.textlength(text, font=font)
    draw.text(
        ((image.size[0] - width) / 2, image.size[1] - FONT_SIZE - 8),
        text,
        fill="black",
        font=font,
    )

    out = io.BytesIO()
    image.save(out, format="png")
    return out.getvalue()


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


def build_website(src_dir: Path, config, bundle, build_dir: Path | None = None) -> Path:
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
    (build_dir / f"qr-chatmail-invite-{mail_domain}.png").write_bytes(
        gen_qr_png(mail_domain, bundle)
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
