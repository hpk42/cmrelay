"""The web pages of a relay, rendered from /opt/cmrelay/www-src/, seeded once."""

import io
from pathlib import Path

import markdown
import qrcode
import qrcode.image.svg
from jinja2 import Template, TemplateError

from ... import CmrelayError, paths
from ...system import fsops

# what deploy last published under /var/www/html
FILES = paths.STATE / "www-files"
LAYOUT = "page-layout.html"
DISABLED = "disabled"


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
    """The DCACCOUNT invite code, as an SVG: a PNG would need Pillow, 74 MiB."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4
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
    # Jinja inside the markdown shows up only after the layout is expanded.
    while True:
        new = Template(content).render(config=config, **render_vars)
        if new == content:
            return new
        content = new


def render_site(src_dir: Path, config) -> dict:
    """Render src_dir into {filename: bytes}, without touching the filesystem."""
    src_dir = Path(src_dir)
    layout_path = src_dir / LAYOUT
    if not layout_path.is_file():
        raise CmrelayError(f"{layout_path} is missing; cannot render pages")
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
            html = render_page(path, layout, config, extra)
            pages[f"{path.stem}.html"] = html.encode()
        elif path.name != LAYOUT and path.is_file():
            pages[path.name] = path.read_bytes()
    return pages


def seed(changes, bundle, out) -> None:
    """Give the operator a page source, seeded once and theirs from then on."""
    if paths.WWW_SRC.exists():
        return
    # the pages beside this module, not the module
    for path in sorted((bundle.package / "stack/website").iterdir()):
        if path.is_file() and path.suffix != ".py":
            changes.copy("website", path, paths.WWW_SRC / path.name)
    out.detail(f"seeded {paths.WWW_SRC} from the release's own pages")


def publish(changes, config, out) -> None:
    """Render and write the pages, removing only those cmrelay wrote and no longer makes."""
    if config.www_folder.strip() == DISABLED:
        out.detail("no web pages: www_folder is 'disabled'")
        pages = {}
    else:
        pages = render_site(paths.WWW_SRC, config)
        changes.ensure_directory("website", paths.WWW_ROOT)
    for name, data in sorted(pages.items()):
        changes.write("website", paths.WWW_ROOT / name, data)

    previous = set(fsops.read_lines(FILES))
    for name in sorted(previous - set(pages)):
        changes.remove_file("website", paths.WWW_ROOT / name)
    changes.write("website", FILES, "\n".join(sorted(pages)) + "\n")


def configure(deploy):
    seed(deploy.changes, deploy.bundle, deploy.out)
    try:
        publish(deploy.changes, deploy.config, deploy.out)
    except (CmrelayError, TemplateError) as exc:
        # a broken page of the operator's must not fail a deploy
        deploy.out.warn(f"web page build failed, skipping website: {exc}")


def units(deploy) -> list:
    return []
