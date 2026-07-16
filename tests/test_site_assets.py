from pathlib import Path


SITE = Path(__file__).parents[1] / "site"


def test_site_declares_and_contains_browser_icons():
    index = (SITE / "index.html").read_text()

    assert '<link rel="icon" href="/favicon.ico"' in index
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in index
    assert (SITE / "favicon.ico").is_file()
    assert (SITE / "favicon.svg").is_file()
    assert (SITE / "apple-touch-icon.png").is_file()
    assert (SITE / "apple-touch-icon-precomposed.png").is_file()


def test_site_icons_are_valid_png_assets():
    assert (SITE / "apple-touch-icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (SITE / "apple-touch-icon-precomposed.png").read_bytes().startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    assert (SITE / "favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")
