"""Tests for i18n module and translation integrity."""

import json
import re
from pathlib import Path

from app.i18n import I18n, SUPPORTED_LOCALES

I18N_DIR = Path(__file__).parent.parent / "app" / "i18n"


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict to dot-notation keys."""
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, str):
            out[key] = v
    return out


def test_supported_locales_files_exist():
    """Ensure every supported locale has an associated json file."""
    for locale in SUPPORTED_LOCALES:
        file_path = I18N_DIR / f"{locale}.json"
        assert file_path.exists(), f"Missing translation file for locale: {locale}"


def test_translation_key_parity():
    """Ensure all locales have 100% key parity with the English source."""
    en_file = I18N_DIR / "en.json"
    assert en_file.exists()
    with open(en_file, "r", encoding="utf-8") as f:
        en_data = json.load(f)
    en_keys = set(flatten(en_data).keys())

    for locale in SUPPORTED_LOCALES:
        if locale == "en":
            continue
        loc_file = I18N_DIR / f"{locale}.json"
        with open(loc_file, "r", encoding="utf-8") as f:
            loc_data = json.load(f)
        loc_keys = set(flatten(loc_data).keys())

        missing = en_keys - loc_keys
        assert not missing, f"Locale '{locale}' missing keys: {missing}"


def test_dashboard_markup_translation_keys_exist_in_every_locale():
    """Prevent visible dashboard elements from rendering raw translation keys."""
    dashboard = I18N_DIR.parent.parent / "web" / "dashboard.html"
    markup = dashboard.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder)?=["\']([^"\']+)', markup))

    for locale in SUPPORTED_LOCALES:
        messages = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        missing = keys - set(flatten(messages))
        assert not missing, f"Dashboard locale '{locale}' missing visible keys: {sorted(missing)}"


def test_i18n_translation_lookup():
    """Test standard i18n lookup and fallback behavior."""
    i18n_th = I18n("th")
    assert i18n_th.t("dashboard.connection.loading_profile") == "กำลังโหลดโปรไฟล์ TikTok…"

    # Non-existent key should return key name
    assert i18n_th.t("non.existent.key") == "non.existent.key"

    # Parameter interpolation
    greeting = i18n_th.t("creator_info.connected", name="John")
    assert "John" in greeting
