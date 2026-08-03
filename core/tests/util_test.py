# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import json
import re
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

from hscommon.testutil import eq_

from core.util import (
    check_for_update,
    fix_surrogate_encoding,
    format_dupe_count,
    format_perc,
    format_timestamp,
    format_words,
    _parse_release_version,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_perc():
    eq_(format_perc(100), "100")
    eq_(format_perc(0), "0")
    eq_(format_perc(42.6), "43")


def test_format_dupe_count():
    eq_(format_dupe_count(0), "---")
    eq_(format_dupe_count(3), "3")


def test_format_timestamp_zero_is_placeholder():
    eq_(format_timestamp(0, delta=False), "---")
    eq_(format_timestamp(-1, delta=False), "---")


def test_format_timestamp_renders_local_time():
    # Formatted through localtime, so assert the shape rather than a fixed instant.
    result = format_timestamp(1_000_000_000, delta=False)
    assert re.fullmatch(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", result), result


def test_format_timestamp_delta_uses_decimal_form():
    # delta=True routes through hscommon.util.format_time_decimal, which renders a
    # human-readable duration rather than a clock time.
    eq_(format_timestamp(90, delta=True), "1.5 minutes")
    eq_(format_timestamp(30, delta=True), "30.0 seconds")


def test_format_words_flattens_nested_lists():
    eq_(format_words(["foo", "bar"]), "foo, bar")
    eq_(format_words([["foo", "bar"], "baz"]), "(foo, bar), baz")


def test_format_words_strips_newlines():
    eq_(format_words(["foo\nbar"]), "foo bar")


def test_fix_surrogate_encoding_passes_clean_strings_through():
    eq_(fix_surrogate_encoding("perfectly normal"), "perfectly normal")


def test_fix_surrogate_encoding_replaces_unencodable():
    # A lone surrogate cannot be encoded to utf-8; the function must not raise.
    result = fix_surrogate_encoding("bad\udcff")
    assert isinstance(result, str)
    result.encode("utf-8")  # must not raise


# ---------------------------------------------------------------------------
# Release version parsing
# ---------------------------------------------------------------------------


def test_parse_release_version_prefers_tag_over_free_form_name():
    """A release *name* is free-form text; the tag is the reliable source.

    This is the crash from issue #19: v4.4.0 of this fork was published with the
    name "v4.4.0 - first release of the fork", which is not semver.
    """
    release = {"tag_name": "v4.4.0", "name": "v4.4.0 - first release of the fork"}
    eq_(str(_parse_release_version(release)), "4.4.0")


def test_parse_release_version_strips_v_prefix():
    eq_(str(_parse_release_version({"tag_name": "v1.2.3"})), "1.2.3")
    eq_(str(_parse_release_version({"tag_name": "V1.2.3"})), "1.2.3")
    eq_(str(_parse_release_version({"tag_name": "1.2.3"})), "1.2.3")


def test_parse_release_version_falls_back_to_name():
    eq_(str(_parse_release_version({"tag_name": "nonsense", "name": "2.0.0"})), "2.0.0")


def test_parse_release_version_returns_none_when_unparseable():
    assert _parse_release_version({"tag_name": "Summer build", "name": "latest"}) is None
    assert _parse_release_version({}) is None
    assert _parse_release_version({"tag_name": "", "name": ""}) is None
    assert _parse_release_version({"tag_name": None, "name": 42}) is None


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@contextmanager
def _releases(payload, status=200):
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload, status)):
        yield


def _release(tag, url=None, name=None):
    return {
        "tag_name": tag,
        "name": name if name is not None else tag,
        "html_url": url or f"https://example.invalid/releases/{tag}",
    }


def test_newer_release_is_offered():
    with _releases([_release("v4.5.0")]):
        result = check_for_update("4.4.0")
    assert result is not None
    eq_(str(result["version"]), "4.5.0")
    eq_(result["url"], "https://example.invalid/releases/v4.5.0")


def test_same_version_offers_nothing():
    with _releases([_release("v4.4.0")]):
        assert check_for_update("4.4.0") is None


def test_older_release_offers_nothing():
    with _releases([_release("v4.3.1")]):
        assert check_for_update("4.4.0") is None


def test_highest_of_several_releases_wins():
    with _releases([_release("v4.5.0"), _release("v4.7.2"), _release("v4.6.0")]):
        result = check_for_update("4.4.0")
    eq_(str(result["version"]), "4.7.2")
    eq_(result["url"], "https://example.invalid/releases/v4.7.2")


def test_prerelease_excluded_by_default():
    with _releases([_release("v4.5.0-beta.1")]):
        assert check_for_update("4.4.0") is None


def test_prerelease_included_when_requested():
    with _releases([_release("v4.5.0-beta.1")]):
        result = check_for_update("4.4.0", include_prerelease=True)
    eq_(str(result["version"]), "4.5.0-beta.1")


def test_unparseable_release_is_skipped_not_fatal():
    """Issue #19: a single bad release name used to raise out of the whole call."""
    payload = [
        {"tag_name": "Summer build", "name": "Summer build", "html_url": "https://example.invalid/x"},
        _release("v4.5.0"),
    ]
    with _releases(payload):
        result = check_for_update("4.4.0")
    eq_(str(result["version"]), "4.5.0")


def test_release_missing_html_url_is_skipped():
    with _releases([{"tag_name": "v9.9.9", "name": "9.9.9"}]):
        assert check_for_update("4.4.0") is None


def test_malformed_entries_are_skipped():
    with _releases(["not a dict", 42, None, _release("v5.0.0")]):
        result = check_for_update("4.4.0")
    eq_(str(result["version"]), "5.0.0")


def test_non_semver_current_version_returns_none():
    with _releases([_release("v4.5.0")]):
        assert check_for_update("not-a-version") is None


def test_non_200_status_returns_none():
    with _releases([_release("v4.5.0")], status=500):
        assert check_for_update("4.4.0") is None


def test_invalid_json_returns_none():
    with _releases(b"{not json at all"):
        assert check_for_update("4.4.0") is None


def test_non_list_payload_returns_none():
    with _releases({"message": "Not Found"}):
        assert check_for_update("4.4.0") is None


def test_network_error_returns_none():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route to host")):
        assert check_for_update("4.4.0") is None


def test_empty_release_list_returns_none():
    with _releases([]):
        assert check_for_update("4.4.0") is None
