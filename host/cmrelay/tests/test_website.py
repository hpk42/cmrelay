"""Which pages get published, and which stale ones go."""

from types import SimpleNamespace

from cmrelay import paths
from cmrelay.stack.website import website
from cmrelay.system import fsops


class Config(SimpleNamespace):
    mail_domain = "relay.example.org"
    username_min_length = 3
    username_max_length = 9
    password_min_length = 9
    www_folder = ""


def publish(**config):
    website.publish(fsops.Changes(), Config(**config), SimpleNamespace(detail=print))


def test_a_stale_page_goes_and_a_file_dropped_in_by_hand_stays(fake_root):
    paths.WWW_SRC.mkdir(parents=True)
    (paths.WWW_SRC / "page-layout.html").write_text("<html>{{ markdown_html }}</html>")
    for name in ("index", "info"):
        (paths.WWW_SRC / f"{name}.md").write_text(f"# {name}\n")
    publish()
    assert (paths.WWW_ROOT / "info.html").is_file()
    keybase = paths.WWW_ROOT / "keybase.txt"
    keybase.write_text("theirs\n")

    (paths.WWW_SRC / "info.md").unlink()
    publish()
    assert not (paths.WWW_ROOT / "info.html").exists()
    assert keybase.read_text() == "theirs\n"

    publish(www_folder="disabled")
    assert not (paths.WWW_ROOT / "index.html").exists()
    assert keybase.read_text() == "theirs\n"


def test_publishing_nothing_leaves_the_document_root_alone(fake_root):
    paths.WWW_ROOT.mkdir(parents=True)
    (paths.WWW_ROOT / "index.html").write_text("somebody else's\n")
    publish(www_folder="disabled")
    assert (paths.WWW_ROOT / "index.html").read_text() == "somebody else's\n"


def test_the_operators_directory_is_seeded_once(fake_root, bundle, out):
    website.seed(fsops.Changes(), bundle, out)
    assert (paths.WWW_SRC / "index.md").is_file()
    (paths.WWW_SRC / "index.md").write_text("# my own page\n")

    website.seed(fsops.Changes(), bundle, out)
    assert (paths.WWW_SRC / "index.md").read_text() == "# my own page\n"
