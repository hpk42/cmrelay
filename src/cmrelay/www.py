"""Which pages this relay publishes, and where their source lives.

cmdeploy reads `www_folder` as a directory on the operator's laptop. There is
no laptop here, so it is a path on the relay host:

    /opt/cmrelay/www/       the operator's page source, seeded once
    /opt/cmrelay/www.dist/  the release's own copy, replaced every deploy

www.dist is what a deploy diffs against to name pages that have diverged.
"""

from pathlib import Path

from . import paths, state, website

DISABLED = "disabled"


class Source:
    """Where the pages come from, and whether there are any."""

    def __init__(self, path=None, build=True, skip_reason=None, warn=None):
        self.path = path
        self.build = build
        self.skip_reason = skip_reason
        self.warn = warn

    @property
    def publishes(self) -> bool:
        return self.path is not None


def resolve(config) -> Source:
    """Turn `www_folder` into a decision, following cmdeploy's own rules.

    A missing directory is skipped loudly rather than silently: an adopted
    ini often names a path on a build machine that is not this host.
    """
    setting = (getattr(config, "www_folder", "") or "").strip()

    if setting == DISABLED:
        return Source(skip_reason="www_folder is 'disabled'")

    if not setting:
        return Source(paths.WWW_SRC)

    folder = Path(setting)
    if not folder.is_dir():
        return Source(
            skip_reason=f"www_folder {folder} does not exist",
            warn=(
                f"www_folder is set to {folder}, which does not exist on this"
                " host. Under cmrelay that is a path on the relay itself, not"
                " on a build machine, so no web pages are being published."
                f" Unset it to use {paths.WWW_SRC}, or set it to 'disabled'."
            ),
        )

    # cmdeploy's rule: src/index.md means build, anything else upload as is
    src = folder / "src"
    if (src / "index.md").is_file():
        return Source(src)
    return Source(folder, build=False)


def seed(journal, bundle, out) -> None:
    """Give the operator a page source, and keep the release's copy beside it.

    Seeded once; from then on /opt/cmrelay/www is the operator's.
    """
    files = [p for p in sorted((bundle.vendor / "www").iterdir()) if p.is_file()]
    if not paths.WWW_SRC.exists():
        for path in files:
            journal.copy("website", path, paths.WWW_SRC / path.name)
        out.detail(f"seeded {paths.WWW_SRC} from the release's own pages")

    published = {p.name for p in files}
    for path in files:
        journal.copy("website", path, paths.WWW_DIST / path.name)
    if paths.WWW_DIST.is_dir():
        for stale in sorted(paths.WWW_DIST.iterdir()):
            if stale.is_file() and stale.name not in published:
                journal.remove_file("website", stale)


def report_divergence(out, version: str) -> None:
    """Name the operator's pages that no longer match the release originals."""
    if not (paths.WWW_SRC.is_dir() and paths.WWW_DIST.is_dir()):
        return
    differing = []
    for original in sorted(paths.WWW_DIST.iterdir()):
        mine = paths.WWW_SRC / original.name
        if not (original.is_file() and mine.is_file()):
            continue
        if original.read_bytes() != mine.read_bytes():
            differing.append(original.name)
    if not differing:
        return
    out.warn(
        f"{len(differing)} of your web pages differ from the {version} originals:"
        f"\n    {', '.join(differing)}"
        f"\n  Compare with: diff -u {paths.WWW_DIST}/<name> {paths.WWW_SRC}/<name>"
    )


def publish(journal, config, source: Source, out) -> None:
    """Render and write the pages, removing the ones no longer produced.

    Stale removal is restricted to what cmrelay last wrote, so a file the
    operator dropped into /var/www/html by hand stays.
    """
    if not source.publishes:
        out.detail(f"no web pages: {source.skip_reason}")
        return

    if source.build:
        pages = website.render_site(source.path, config)
    else:
        pages = website.copy_tree(source.path)

    journal.ensure_directory("website", paths.WWW_ROOT)
    for name, data in sorted(pages.items()):
        journal.write("website", paths.WWW_ROOT / name, data)

    previous = set(state.read_www_files())
    for name in sorted(previous - set(pages)):
        journal.remove_file("website", paths.WWW_ROOT / name)
    state.save_www_files(journal, pages)
