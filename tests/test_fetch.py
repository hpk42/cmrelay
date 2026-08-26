"""Where a pip-installed cmrelay gets its payload from.

A release wheel carries one; a wheel built from a source checkout does not
and falls back to downloading. Which of the two is in play decides whether
`cmrelay install` needs the network at all.
"""

import pytest

from cmrelay import fetch


def test_no_bundled_payload_in_a_source_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "BUNDLED", tmp_path / "absent.run")
    assert fetch.bundled_payload() is None


def test_a_bundled_payload_is_found(monkeypatch, tmp_path):
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(fetch, "BUNDLED", run)
    assert fetch.bundled_payload() == run


def test_an_unreleased_version_has_nothing_to_download(monkeypatch):
    """The message has to say what to do, not just that it failed."""
    monkeypatch.setattr(fetch, "__version__", "0.0.0.dev0")
    with pytest.raises(fetch.FetchError, match="nix build"):
        fetch.payload_url()


def test_the_asset_name_is_the_contract_with_the_release(monkeypatch):
    monkeypatch.setattr(fetch, "__version__", "1.2.3")
    url = fetch.payload_url(arch="aarch64")
    assert url.endswith("/v1.2.3/cmrelay-1.2.3-aarch64-linux.run")
