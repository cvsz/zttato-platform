import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const i18nSource = readFileSync(new URL("../web/i18n.js", import.meta.url), "utf8");
const enJson = JSON.parse(readFileSync(new URL("../app/i18n/en.json", import.meta.url), "utf8"));
const thJson = JSON.parse(readFileSync(new URL("../app/i18n/th.json", import.meta.url), "utf8"));

function createI18nContext({ storedLocale = null } = {}) {
  const store = new Map();
  if (storedLocale) store.set("zttato_locale", storedLocale);

  const localStorage = {
    getItem: (key) => store.get(key) || null,
    setItem: (key, val) => store.set(key, String(val)),
  };

  const elements = [];
  const document = {
    readyState: "complete",
    documentElement: { lang: "en" },
    querySelectorAll: (selector) => {
      if (selector.includes("[data-i18n]")) return elements;
      if (selector.includes(".lang-select")) return [];
      return [];
    },
    addEventListener: () => {},
  };

  const fetch = async (url) => {
    if (url.includes("/i18n/th.json")) return { ok: true, json: async () => thJson };
    if (url.includes("/i18n/en.json")) return { ok: true, json: async () => enJson };
    return { ok: false, status: 404 };
  };

  const sandbox = {
    document,
    localStorage,
    fetch,
    navigator: { language: "en-US" },
    setTimeout: (fn) => setTimeout(fn, 0),
    clearTimeout: (id) => clearTimeout(id),
    console,
  };

  runInNewContext(i18nSource, sandbox, { filename: "i18n.js" });
  return { i18n: sandbox.i18n, elements, store };
}

test("i18n initializes with default and translates strings", async () => {
  const { i18n } = createI18nContext();
  await i18n.init("en");
  assert.equal(i18n.getLocale(), "en");
  assert.equal(i18n.t("nav.home"), "Home");
  assert.equal(i18n.t("landing.hero.headline"), "Your video, your choice, your account.");
});

test("i18n dynamically switches locale in real-time and persists to localStorage", async () => {
  const { i18n, store } = createI18nContext();
  await i18n.init("en");
  assert.equal(i18n.getLocale(), "en");

  let notifiedLocale = null;
  i18n.subscribe((locale) => {
    notifiedLocale = locale;
  });

  await i18n.setLocale("th");
  assert.equal(i18n.getLocale(), "th");
  assert.equal(notifiedLocale, "th");
  assert.equal(store.get("zttato_locale"), "th");
  assert.equal(i18n.t("nav.home"), "หน้าหลัก");
  assert.equal(i18n.t("landing.hero.headline"), "วิดีโอของคุณ ตัวเลือกของคุณ บัญชีของคุณ");
});

test("i18n restores saved locale from localStorage on init", async () => {
  const { i18n } = createI18nContext({ storedLocale: "th" });
  await i18n.init();
  assert.equal(i18n.getLocale(), "th");
  assert.equal(i18n.t("nav.home"), "หน้าหลัก");
});
