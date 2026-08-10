from datetime import datetime
from pathlib import Path


SITE = Path(__file__).parents[1] / "site"


def test_site_denies_dotfiles_without_error_logging():
    nginx_conf = (SITE / "nginx.conf").read_text()

    assert "location ~ /\\.(?!well-known(?:/|$))" in nginx_conf
    assert "access_log off;" in nginx_conf
    assert "log_not_found off;" in nginx_conf
    assert "return 404;" in nginx_conf
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in (SITE / "Dockerfile").read_text()


def test_site_preserves_well_known_exception():
    nginx_conf = (SITE / "nginx.conf").read_text()

    assert "well-known(?:/|$)" in nginx_conf
    assert (SITE / ".well-known" / "security.txt").is_file()


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


def test_site_publishes_security_txt_with_required_fields():
    security_txt = SITE / ".well-known" / "security.txt"
    assert security_txt.is_file()

    fields = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in security_txt.read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert fields["Contact"].startswith("https://")
    assert datetime.fromisoformat(fields["Expires"].replace("Z", "+00:00")).tzinfo
