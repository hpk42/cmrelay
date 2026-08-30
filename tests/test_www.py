"""Which pages get published, from where, and which stale ones go."""

from types import SimpleNamespace

import pytest

from cmrelay import fsops, paths, state, website, www
from cmrelay.output import Out


class Config(SimpleNamespace):
    mail_domain = "relay.example.org"
    username_min_length = 3
    username_max_length = 9
    password_min_length = 9
    www_folder = ""


def site(directory, index="# hello\n"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page-layout.html").write_text("<html>{{ markdown_html }}</html>")
    (directory / "index.md").write_text(index)
    return directory


# -- www_folder ------------------------------------------------------------


def test_unset_uses_cmrelays_own_directory(fake_root):
    assert www.resolve(Config()).path == paths.WWW_SRC


def test_disabled_publishes_nothing():
    source = www.resolve(Config(www_folder="disabled"))
    assert source.publishes is False
    assert "disabled" in source.skip_reason


def test_a_tree_with_src_index_md_is_built(tmp_path):
    site(tmp_path / "mysite/src")
    source = www.resolve(Config(www_folder=str(tmp_path / "mysite")))
    assert source.path == tmp_path / "mysite/src"
    assert source.build is True


def test_a_tree_without_src_index_md_is_uploaded_as_it_stands(tmp_path):
    (tmp_path / "mysite").mkdir()
    (tmp_path / "mysite/index.html").write_text("<p>hi</p>")
    source = www.resolve(Config(www_folder=str(tmp_path / "mysite")))
    assert source.path == tmp_path / "mysite"
    assert source.build is False


def test_a_missing_directory_warns_loudly_rather_than_skipping_quietly(tmp_path):
    """An adopted ini names a path on a build machine that is not this host."""
    source = www.resolve(Config(www_folder=str(tmp_path / "gone")))
    assert source.publishes is False
    assert "on the relay itself" in source.warn


# -- rendering -------------------------------------------------------------


def test_a_page_carrying_merge_markers_is_refused(tmp_path):
    src = site(tmp_path / "src")
    (src / "info.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n")
    with pytest.raises(website.WebsiteError, match="merge conflict"):
        website.render_site(src, Config())


def test_a_layout_is_required(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text("# hi\n")
    with pytest.raises(website.WebsiteError, match="page-layout"):
        website.render_site(src, Config())


def test_rendering_yields_pages_and_a_qr_code(tmp_path):
    pages = website.render_site(site(tmp_path / "src"), Config())
    assert "index.html" in pages
    assert "qr-chatmail-invite-relay.example.org.svg" in pages
    assert b"hello" in pages["index.html"]


def test_jinja_inside_the_markdown_is_expanded(tmp_path):
    src = site(tmp_path / "src", index="min {{ username_min_length }}\n")
    pages = website.render_site(src, Config())
    assert b"min three" in pages["index.html"]


# -- publishing ------------------------------------------------------------


def test_publishing_writes_the_pages_and_records_them(fake_root, out):
    journal = fsops.Journal()
    source = www.Source(site(fake_root / "src"))
    www.publish(journal, Config(), source, out)

    assert (paths.WWW_ROOT / "index.html").is_file()
    assert "index.html" in state.read_www_files()


def test_a_page_the_source_no_longer_produces_stops_being_served(fake_root, out):
    src = site(fake_root / "src")
    (src / "info.md").write_text("# info\n")
    www.publish(fsops.Journal(), Config(), www.Source(src), out)
    assert (paths.WWW_ROOT / "info.html").is_file()

    (src / "info.md").unlink()
    www.publish(fsops.Journal(), Config(), www.Source(src), out)
    assert not (paths.WWW_ROOT / "info.html").exists()


def test_a_file_the_operator_dropped_in_by_hand_is_kept(fake_root, out):
    src = site(fake_root / "src")
    www.publish(fsops.Journal(), Config(), www.Source(src), out)
    keybase = paths.WWW_ROOT / "keybase.txt"
    keybase.write_text("theirs\n")

    www.publish(fsops.Journal(), Config(), www.Source(src), out)
    assert keybase.read_text() == "theirs\n"


def test_publishing_nothing_leaves_the_document_root_alone(fake_root, out):
    paths.WWW_ROOT.mkdir(parents=True)
    (paths.WWW_ROOT / "index.html").write_text("somebody else's\n")
    www.publish(
        fsops.Journal(), Config(), www.resolve(Config(www_folder="disabled")), out
    )
    assert (paths.WWW_ROOT / "index.html").read_text() == "somebody else's\n"


# -- seeding and divergence ------------------------------------------------


def test_the_first_deploy_seeds_the_operators_directory(fake_root, bundle, out):
    www.seed(fsops.Journal(), bundle, out)
    assert (paths.WWW_SRC / "index.md").is_file()
    assert (paths.WWW_DIST / "index.md").is_file()


def test_a_later_deploy_replaces_the_dist_copy_and_not_the_operators(
    fake_root, bundle, out
):
    www.seed(fsops.Journal(), bundle, out)
    (paths.WWW_SRC / "index.md").write_text("# my own page\n")
    (paths.WWW_DIST / "index.md").write_text("# stale\n")

    www.seed(fsops.Journal(), bundle, out)
    assert (paths.WWW_SRC / "index.md").read_text() == "# my own page\n"
    assert (paths.WWW_DIST / "index.md").read_text() != "# stale\n"


def test_divergence_from_the_release_originals_is_named(fake_root, bundle, capsys):
    out = Out(colour=False)
    www.seed(fsops.Journal(), bundle, out)
    (paths.WWW_SRC / "index.md").write_text("# my own page\n")

    www.report_divergence(out, "0.4.0")
    printed = capsys.readouterr().out
    assert "differ from the 0.4.0 originals" in printed
    assert "index.md" in printed


def test_a_page_name_with_a_space_survives_the_state_round_trip(fake_root):
    from cmrelay import state
    from cmrelay.fsops import Journal

    state.save_www_files(Journal(), ["press kit.pdf", "index.html"])
    assert state.read_www_files() == ["index.html", "press kit.pdf"]
