"""Internationalization (i18n) utilities for zTTato."""

import json
from pathlib import Path
from typing import Any, Dict, Optional


I18N_DIR = Path(__file__).parent
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ["en", "th", "zh", "ja", "ko", "vi"]


class I18n:
    """Internationalization manager for server-side translations."""

    def __init__(self, locale: str = DEFAULT_LOCALE):
        self.locale = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
        self._translations: Dict[str, Any] = {}
        self._load_translations()

    def _load_translations(self) -> None:
        """Load translations for the current locale."""
        translation_file = I18N_DIR / f"{self.locale}.json"
        if translation_file.exists():
            with open(translation_file, "r", encoding="utf-8") as f:
                self._translations = json.load(f)
        else:
            # Fallback to English
            fallback_file = I18N_DIR / f"{DEFAULT_LOCALE}.json"
            if fallback_file.exists():
                with open(fallback_file, "r", encoding="utf-8") as f:
                    self._translations = json.load(f)
            else:
                self._translations = {}

    def t(self, key: str, **kwargs) -> str:
        """Translate a key with optional parameter interpolation.

        Args:
            key: Dot-separated translation key (e.g., "dashboard.video.title")
            **kwargs: Parameters for string interpolation

        Returns:
            Translated string with interpolated parameters
        """
        keys = key.split(".")
        value: Any = self._translations

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                # Fallback to key if translation not found
                return key

        if isinstance(value, str):
            # Interpolate parameters
            try:
                return value.format(**kwargs)
            except (KeyError, ValueError):
                return value

        return key

    def get_locale(self) -> str:
        """Get current locale."""
        return self.locale

    def set_locale(self, locale: str) -> None:
        """Set locale and reload translations."""
        if locale in SUPPORTED_LOCALES:
            self.locale = locale
            self._load_translations()

    def get_supported_locales(self) -> list[str]:
        """Get list of supported locales."""
        return SUPPORTED_LOCALES.copy()


# Global i18n instance
_i18n_instance: Optional[I18n] = None


def get_i18n(locale: Optional[str] = None) -> I18n:
    """Get or create global i18n instance."""
    global _i18n_instance
    if _i18n_instance is None or locale is not None:
        _i18n_instance = I18n(locale or DEFAULT_LOCALE)
    return _i18n_instance


def t(key: str, **kwargs) -> str:
    """Convenience function for translation."""
    return get_i18n().t(key, **kwargs)


def get_locale() -> str:
    """Get current locale."""
    return get_i18n().get_locale()


def set_locale(locale: str) -> None:
    """Set global locale."""
    get_i18n().set_locale(locale)
