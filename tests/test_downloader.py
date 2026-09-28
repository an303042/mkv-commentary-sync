from unittest.mock import patch

import pytest

from core.downloader import open_mkvtoolnix_page


@pytest.mark.parametrize(
    ("platform", "expected_suffix"),
    [
        ("win32", "#windows"),
        ("darwin", "#macos"),
        ("linux", "downloads.html"),
    ],
)
def test_mkvtoolnix_download_page_matches_platform(platform, expected_suffix):
    with patch("core.downloader.sys.platform", platform), patch(
        "core.downloader.webbrowser.open"
    ) as open_browser:
        open_mkvtoolnix_page()

    assert open_browser.call_args.args[0].endswith(expected_suffix)
