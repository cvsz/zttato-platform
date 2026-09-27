"""Tests for the translation CLI credential boundary."""

import json
import importlib.util
import sys
from pathlib import Path

import pytest

TRANSLATOR_PATH = Path(__file__).parent.parent / "scripts" / "translate_i18n.py"
TRANSLATOR_SPEC = importlib.util.spec_from_file_location("zttato_translate_i18n", TRANSLATOR_PATH)
assert TRANSLATOR_SPEC is not None and TRANSLATOR_SPEC.loader is not None
translate_i18n = importlib.util.module_from_spec(TRANSLATOR_SPEC)
TRANSLATOR_SPEC.loader.exec_module(translate_i18n)


def _write_locale_files(tmp_path: Path, *, target: dict) -> Path:
    locale_dir = tmp_path / "i18n"
    locale_dir.mkdir()
    (locale_dir / "en.json").write_text(json.dumps({"greeting": "Hello"}), encoding="utf-8")
    (locale_dir / "th.json").write_text(json.dumps(target), encoding="utf-8")
    return locale_dir


def test_dry_run_does_not_require_or_read_api_credentials(tmp_path, monkeypatch, capsys):
    locale_dir = _write_locale_files(tmp_path, target={})
    original_target = (locale_dir / "th.json").read_text(encoding="utf-8")
    monkeypatch.setattr(translate_i18n, "I18N_DIR", locale_dir)
    monkeypatch.setattr(sys, "argv", ["translate_i18n.py", "--dry-run", "--locale", "th"])
    monkeypatch.delenv(translate_i18n.API_KEY_ENV, raising=False)

    def unexpected_home_access():
        raise AssertionError("dry-run must not read the credential file")

    monkeypatch.setattr(Path, "home", unexpected_home_access)

    translate_i18n.main()

    output = capsys.readouterr().out
    assert "1 missing keys to translate" in output
    assert '[greeting] = "Hello"' in output
    assert (locale_dir / "th.json").read_text(encoding="utf-8") == original_target


def test_translation_passes_credential_to_provider_without_printing_it(tmp_path, monkeypatch, capsys):
    locale_dir = _write_locale_files(tmp_path, target={})
    credential = "redaction-canary"
    monkeypatch.setattr(translate_i18n, "I18N_DIR", locale_dir)
    monkeypatch.setattr(sys, "argv", ["translate_i18n.py", "--locale", "th"])
    monkeypatch.setenv(translate_i18n.API_KEY_ENV, credential)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def fake_translate_batch(api_key, texts, target_language, locale_code, model):
        assert api_key == credential
        assert texts == {"greeting": "Hello"}
        assert target_language == "Thai (ภาษาไทย)"
        assert locale_code == "th"
        assert model == translate_i18n.MODEL
        return {"greeting": "สวัสดี"}

    monkeypatch.setattr(translate_i18n, "translate_batch", fake_translate_batch)

    translate_i18n.main()

    output = capsys.readouterr().out
    assert credential not in output
    assert json.loads((locale_dir / "th.json").read_text(encoding="utf-8")) == {"greeting": "สวัสดี"}


def test_translation_without_credentials_fails_closed_without_disclosing_env_name(tmp_path, monkeypatch, capsys):
    locale_dir = _write_locale_files(tmp_path, target={})
    monkeypatch.setattr(translate_i18n, "I18N_DIR", locale_dir)
    monkeypatch.setattr(sys, "argv", ["translate_i18n.py", "--locale", "th"])
    monkeypatch.setenv(translate_i18n.API_KEY_ENV, "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        translate_i18n.main()

    output = capsys.readouterr().out
    assert exit_info.value.code == 1
    assert "Translation API credentials are not configured" in output
    assert translate_i18n.API_KEY_ENV not in output
