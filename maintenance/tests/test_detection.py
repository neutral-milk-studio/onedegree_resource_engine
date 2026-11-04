"""Tests for the detection pipeline: website source, extractor, comparator, worker, scheduler."""

from __future__ import annotations

import json
import os
import socket

import pytest

from contracts.website_source import WebsiteSource
from maintenance.website_source.sample import SampleWebsiteSource, normalize_url, strip_comments

BAYVIEW_URL = "https://bayview-foodpantry.example.org/grocery"
EASTSIDE_URL = "https://eastside-legal.example.org/housing"
HARBOR_URL = "https://harbor-youth.example.org/shelter"


# --------------------------------------------------------------------------- #
# Task 1: sample website source
# --------------------------------------------------------------------------- #


def test_sample_source_satisfies_the_contract():
    assert isinstance(SampleWebsiteSource(), WebsiteSource)


def test_sample_source_returns_seeded_page():
    result = SampleWebsiteSource().get(BAYVIEW_URL)
    assert result.status == 200
    assert result.error is None
    assert "San Francisco and San Mateo Counties" in result.content


def test_sample_source_hides_the_answer_comment():
    # The seeded pages name their expected category in an HTML comment.
    for url in (BAYVIEW_URL, EASTSIDE_URL):
        content = SampleWebsiteSource().get(url).content
        assert "<!--" not in content
        assert "SEEDED" not in content
        assert "Expected category" not in content


def test_sample_source_returns_seeded_unreachable_site():
    result = SampleWebsiteSource().get(HARBOR_URL)
    assert result.content is None
    assert result.status is None
    assert result.error == "unreachable"


def test_sample_source_unknown_url_is_not_found():
    result = SampleWebsiteSource().get("https://nowhere.example.org/")
    assert (result.content, result.status, result.error) == (None, 404, "not found")


@pytest.mark.parametrize(
    "variant",
    [
        BAYVIEW_URL + "/",
        "HTTPS://BAYVIEW-FOODPANTRY.EXAMPLE.ORG/grocery",  # scheme and host ignore case
        BAYVIEW_URL + "#top",
    ],
)
def test_sample_source_matches_equivalent_urls(variant):
    assert SampleWebsiteSource().get(variant).status == 200


def test_sample_source_paths_are_case_sensitive():
    assert SampleWebsiteSource().get(BAYVIEW_URL.upper()).status == 404


def test_normalize_url_keeps_the_query():
    assert normalize_url("https://A.org/x/?id=1#f") == "https://a.org/x?id=1"


def _content_dir(tmp_path, entries, pages=None):
    (tmp_path / "manifest.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    for name, body in (pages or {}).items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_sample_source_reports_a_missing_page_file_as_an_error(tmp_path):
    entries = [{"url": "https://a.example.org/x", "page": "gone.html"}]
    result = SampleWebsiteSource(_content_dir(tmp_path, entries)).get("https://a.example.org/x")
    assert result.content is None
    assert result.status is None
    assert result.error.startswith("sample page unreadable")


def test_sample_source_refuses_pages_outside_the_content_dir(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    content = tmp_path / "content"
    content.mkdir()
    entries = [{"url": "https://a.example.org/x", "page": "../secret.txt"}]
    result = SampleWebsiteSource(_content_dir(content, entries)).get("https://a.example.org/x")
    assert result.content is None
    assert result.error == "sample page outside content dir"


def test_sample_source_honors_the_content_dir_env(tmp_path, monkeypatch):
    entries = [{"url": "https://a.example.org/x", "page": "p.html"}]
    _content_dir(tmp_path, entries, {"p.html": "<p>hello</p>"})
    monkeypatch.setenv("RE_SEED_CONTENT_DIR", str(tmp_path))
    assert SampleWebsiteSource().get("https://a.example.org/x").content == "<p>hello</p>"


def test_sample_source_never_uses_the_network(monkeypatch):
    # Requirement 19.2: the default source needs no network access.
    def refuse(*_args, **_kwargs):
        raise AssertionError("the sample website source tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    source = SampleWebsiteSource()
    assert source.get(BAYVIEW_URL).status == 200
    assert source.get(HARBOR_URL).error == "unreachable"
    assert source.get("https://nowhere.example.org/").status == 404


def test_sample_source_reports_an_undecodable_page_as_an_error(tmp_path):
    entries = [{"url": "https://a.example.org/x", "page": "bin.html"}]
    directory = _content_dir(tmp_path, entries)
    (directory / "bin.html").write_bytes(b"\xff\xfe\x00 not utf-8")
    result = SampleWebsiteSource(directory).get("https://a.example.org/x")
    assert (result.content, result.status) == (None, None)
    assert result.error == "sample page unreadable: bin.html"


def test_sample_source_errors_do_not_leak_filesystem_paths(tmp_path):
    entries = [{"url": "https://a.example.org/x", "page": "gone.html"}]
    result = SampleWebsiteSource(_content_dir(tmp_path, entries)).get("https://a.example.org/x")
    assert str(tmp_path) not in result.error


def test_sample_source_refuses_an_absolute_page_path(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    content = tmp_path / "content"
    content.mkdir()
    entries = [{"url": "https://a.example.org/x", "page": str(outside)}]
    result = SampleWebsiteSource(_content_dir(content, entries)).get("https://a.example.org/x")
    assert result.content is None
    assert result.error == "sample page outside content dir"


def test_sample_source_refuses_a_symlink_that_escapes_the_content_dir(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    content = tmp_path / "content"
    content.mkdir()
    os.symlink(outside, content / "link.html")
    entries = [{"url": "https://a.example.org/x", "page": "link.html"}]
    result = SampleWebsiteSource(_content_dir(content, entries)).get("https://a.example.org/x")
    assert result.content is None
    assert result.error == "sample page outside content dir"


def test_sample_source_resolves_a_relative_content_dir_against_the_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a relative path must not depend on the working directory
    monkeypatch.setenv("RE_SEED_CONTENT_DIR", "seed/website_content")
    assert SampleWebsiteSource().get(BAYVIEW_URL).status == 200


def test_sample_source_fails_at_startup_without_a_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        SampleWebsiteSource(tmp_path)


def test_sample_source_fails_at_startup_on_a_malformed_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        SampleWebsiteSource(tmp_path)


def test_sample_source_rejects_duplicate_manifest_urls(tmp_path):
    entries = [
        {"url": "https://a.example.org/x", "page": "a.html"},
        {"url": "HTTPS://A.example.org/x/", "page": "b.html"},  # same page after normalizing
    ]
    with pytest.raises(ValueError, match="duplicate manifest url"):
        SampleWebsiteSource(_content_dir(tmp_path, entries))


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>a</p><!-- hint --><p>b</p>", "<p>a</p><p>b</p>"),
        ("<!-- one --><p>a</p><!-- two -->", "<p>a</p>"),
        ("<!--\nmulti\nline\n--><p>a</p>", "<p>a</p>"),
        ("<p>a</p><!-- never closed\nExpected category: discrepancy", "<p>a</p>"),
        ("<p>no comments</p>", "<p>no comments</p>"),
    ],
)
def test_strip_comments_removes_comments_and_keeps_visible_text(html, expected):
    assert strip_comments(html) == expected
