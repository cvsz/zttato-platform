/**
 * Internationalization (i18n) utilities for zTTato frontend.
 * Supports dynamic language switching and parameter interpolation.
 */

class I18n {
  constructor() {
    this.locale = 'en';
    this.translations = {};
    this.supportedLocales = ['en', 'th', 'zh', 'ja', 'ko', 'vi'];
    this.listeners = new Set();
    this.storageKey = 'zttato_locale';
  }

  /**
   * Initialize i18n with detected or saved locale
   */
  async init(locale = null) {
    let target = locale;
    if (!target && typeof localStorage !== 'undefined') {
      try {
        target = localStorage.getItem(this.storageKey);
      } catch (e) {
        // localStorage might be unavailable/restricted
      }
    }
    if (!target && typeof navigator !== 'undefined' && navigator.language) {
      const lang = navigator.language.slice(0, 2).toLowerCase();
      if (this.supportedLocales.includes(lang)) {
        target = lang;
      }
    }
    this.locale = this.supportedLocales.includes(target) ? target : 'en';
    await this.loadTranslations(this.locale);
    this.applyTranslations();
    return this;
  }

  /**
   * Load translations for a locale
   */
  async loadTranslations(locale) {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), 5000) : null;
    try {
      const opts = controller ? { signal: controller.signal } : {};
      const response = await fetch(`/i18n/${locale}.json`, opts);
      if (response.ok) {
        this.translations = await response.json();
        this.locale = locale;
        if (typeof localStorage !== 'undefined') {
          try {
            localStorage.setItem(this.storageKey, locale);
          } catch (e) {}
        }
        if (typeof document !== 'undefined' && document.documentElement) {
          document.documentElement.lang = locale;
        }
        this.notifyListeners();
      } else {
        console.warn(`Failed to load translations for ${locale}`);
        if (locale !== 'en') {
          await this.loadTranslations('en');
        }
      }
    } catch (error) {
      console.error('Failed to load translations:', error);
      if (locale !== 'en') {
        await this.loadTranslations('en');
      }
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  }

  /**
   * Translate a key with optional parameter interpolation
   * @param {string} key - Dot-separated translation key (e.g., "dashboard.video.title")
   * @param {Object} params - Parameters for interpolation
   * @returns {string} Translated string
   */
  t(key, params = {}) {
    const keys = key.split('.');
    let value = this.translations;

    for (const k of keys) {
      if (value && typeof value === 'object' && k in value) {
        value = value[k];
      } else {
        // Fallback to key if translation not found
        return key;
      }
    }

    if (typeof value === 'string') {
      // Interpolate parameters
      return value.replace(/\{(\w+)\}/g, (match, paramKey) => {
        return params[paramKey] !== undefined ? params[paramKey] : match;
      });
    }

    return key;
  }

  /**
   * Apply translations to all DOM elements with data-i18n attributes
   */
  applyTranslations(root = null) {
    if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') {
      return;
    }
    const container = root || document;

    // Text content
    container.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (key) {
        const translated = this.t(key);
        if (translated !== key) el.textContent = translated;
      }
    });

    // Placeholders
    container.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) {
        const translated = this.t(key);
        if (translated !== key) el.placeholder = translated;
      }
    });

    // Titles / Tooltips
    container.querySelectorAll('[data-i18n-title]').forEach((el) => {
      const key = el.getAttribute('data-i18n-title');
      if (key) {
        const translated = this.t(key);
        if (translated !== key) el.title = translated;
      }
    });

    // Sync any language selector dropdowns on the page
    container.querySelectorAll('.lang-select, #lang-select').forEach((select) => {
      if (select.value !== this.locale) {
        select.value = this.locale;
      }
    });
  }

  /**
   * Attach change listeners to all language selector dropdowns
   */
  setupLanguageSelectors() {
    if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') {
      return;
    }
    document.querySelectorAll('.lang-select, #lang-select').forEach((select) => {
      select.value = this.locale;
      select.addEventListener('change', async (e) => {
        await this.setLocale(e.target.value);
      });
    });
  }

  /**
   * Get current locale
   */
  getLocale() {
    return this.locale;
  }

  /**
   * Set locale, reload translations, and update UI in real-time
   */
  async setLocale(locale) {
    if (this.supportedLocales.includes(locale)) {
      await this.loadTranslations(locale);
      this.applyTranslations();
    }
  }

  /**
   * Get supported locales
   */
  getSupportedLocales() {
    return [...this.supportedLocales];
  }

  /**
   * Subscribe to locale changes
   */
  subscribe(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  notifyListeners() {
    this.listeners.forEach((cb) => {
      try {
        cb(this.locale);
      } catch (err) {
        console.error('Error in i18n subscriber:', err);
      }
    });
  }

  /**
   * Get HTML lang attribute value
   */
  getHtmlLang() {
    return this.locale;
  }

  /**
   * Get direction (ltr/rtl) for current locale
   */
  getDirection() {
    return 'ltr';
  }
}

// Auto-initialize when script loads in browser (DOM ready or immediately)
const i18n = new I18n();

if (typeof window !== 'undefined') {
  const onReady = () => {
    i18n.init().then(() => {
      i18n.setupLanguageSelectors();
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
}

// Convenience function for translations
function t(key, params) {
  return i18n.t(key, params);
}

// Export for different module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { I18n, i18n, t };
}
if (typeof window !== 'undefined') {
  window.I18n = I18n;
  window.i18n = i18n;
  window.t = t;
}
if (typeof globalThis !== 'undefined') {
  globalThis.I18n = I18n;
  globalThis.i18n = i18n;
  globalThis.t = t;
}