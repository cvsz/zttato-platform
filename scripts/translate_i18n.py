#!/usr/bin/env python3
"""
Automated i18n translation script for zTTato Platform.

Fills missing translation keys in all locale files using Gemini Flash.
Preserves existing translations; only adds missing keys.

Usage:
    python3 scripts/translate_i18n.py [--dry-run] [--locale LOCALE]

    --dry-run   Show what would be translated without writing files
    --locale    Translate only a specific locale (default: all non-EN locales)
"""

import json
import os
import sys
import argparse
import copy
import httpx
from pathlib import Path


# ── Config ──────────────────────────────────────────────────────────────────
I18N_DIR = Path(__file__).parent.parent / "app" / "i18n"
DEFAULT_LOCALE = "en"
TARGET_LOCALES = {
    "th": "Thai (ภาษาไทย)",
    "zh": "Simplified Chinese (简体中文)",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "vi": "Vietnamese (Tiếng Việt)",
}
MODEL = "openai/gpt-oss-120b"
API_URL = "https://api.groq.com/openai/v1/chat/completions"
API_KEY_ENV = "GROQ_API_KEY"


# ── Helpers ──────────────────────────────────────────────────────────────────
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


def set_nested(d: dict, key: str, value: str) -> None:
    """Set a value in nested dict using dot-notation key."""
    parts = key.split(".")
    current = d
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base (override wins)."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def get_missing_keys(en: dict, target: dict) -> dict[str, str]:
    """Return flat dict of keys present in en but absent in target."""
    en_flat = flatten(en)
    target_flat = flatten(target)
    return {k: v for k, v in en_flat.items() if k not in target_flat}


def translate_batch(
    api_key: str,
    texts: dict[str, str],
    target_language: str,
    locale_code: str,
    model: str = MODEL,
) -> dict[str, str]:
    """
    Send a batch of key→english_value pairs to OpenRouter/Gemini for translation.
    Returns key→translated_value dict.
    """
    if not texts:
        return {}

    # Build prompt
    items_json = json.dumps(texts, ensure_ascii=False, indent=2)
    prompt = f"""You are a professional UI translator for a video content management platform called zTTato.

Translate the following JSON object from English to {target_language}.
The object maps translation keys (dot-notation) to English UI strings.

Rules:
- Return ONLY a valid JSON object with the SAME keys but translated values
- Keep placeholders like {{name}}, {{error}}, {{scopes}}, {{seconds}}, {{status}} EXACTLY as-is
- Keep brand names: zTTato, TikTok, MP4, TikTok Shop, etc. UNCHANGED
- Keep technical terms unchanged: OAuth, Draft, Direct Post, Login Kit, HTTPS
- Translate naturally for a {target_language} UI/app context
- Preserve punctuation style (e.g., trailing "…" ellipsis)
- Do NOT add explanations, markdown, or any text outside the JSON

Input (English):
{items_json}

Output (JSON only, {target_language}):"""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 8192,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    import time

    for attempt in range(4):
        with httpx.Client(timeout=90.0) as http:
            resp = http.post(API_URL, json=payload, headers=headers)
        if resp.status_code == 429:
            wait = 2**attempt * 5  # 5s, 10s, 20s, 40s
            print(f"\n  ⏳ Rate limited. Waiting {wait}s…", end="", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        raise RuntimeError("Rate limit not resolved after 4 retries")

    raw = data["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        result = json.loads(raw)
        # Validate same keys
        for k in texts:
            if k not in result:
                print(f"  ⚠ Missing key in response: {k} — using English fallback")
                result[k] = texts[k]
        return result
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}")
        print(f"  Raw response: {raw[:300]}")
        # Return English fallback for all keys
        return dict(texts)


def translate_locale(
    api_key: str,
    en_data: dict,
    locale_code: str,
    locale_name: str,
    dry_run: bool = False,
    model: str = MODEL,
) -> tuple[int, int]:
    """
    Translate missing keys for a locale.
    Returns (translated_count, skipped_count).
    """
    locale_file = I18N_DIR / f"{locale_code}.json"

    if locale_file.exists():
        with open(locale_file, "r", encoding="utf-8") as f:
            target_data = json.load(f)
    else:
        target_data = {}
        print(f"  → Creating new locale file for {locale_code}")

    missing = get_missing_keys(en_data, target_data)

    if not missing:
        print(f"  ✓ {locale_code}: All {len(flatten(en_data))} keys present — nothing to do")
        return 0, 0

    print(f"  → {locale_code}: {len(missing)} missing keys to translate")

    if dry_run:
        for k, v in list(missing.items())[:5]:
            print(f'     [{k}] = "{v}"')
        if len(missing) > 5:
            print(f"     ... and {len(missing) - 5} more")
        return len(missing), 0

    # Translate in batches of 40 to stay within token limits
    batch_size = 40
    translated_total = {}
    keys = list(missing.keys())

    for i in range(0, len(keys), batch_size):
        batch_keys = keys[i : i + batch_size]
        batch = {k: missing[k] for k in batch_keys}
        batch_num = i // batch_size + 1
        total_batches = (len(keys) + batch_size - 1) // batch_size
        print(f"     Batch {batch_num}/{total_batches} ({len(batch)} keys)…", end="", flush=True)

        translated = translate_batch(api_key, batch, locale_name, locale_code, model=model)
        translated_total.update(translated)
        print(" ✓")

    # Merge translations into existing data (flat → nested)
    translations_nested: dict = {}
    for key, value in translated_total.items():
        set_nested(translations_nested, key, value)

    merged = deep_merge(target_data, translations_nested)

    if not dry_run:
        with open(locale_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  ✓ Written: {locale_file}")

    return len(translated_total), 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-translate i18n JSON files using Gemini")
    parser.add_argument("--dry-run", action="store_true", help="Show missing keys without translating")
    parser.add_argument(
        "--locale", type=str, help="Translate only a specific locale", choices=list(TARGET_LOCALES.keys())
    )
    parser.add_argument("--model", type=str, default=MODEL, help=f"Gemini model (default: {MODEL})")
    args = parser.parse_args()

    api_key = ""
    if not args.dry_run:
        # Load credentials only when an API request will be made.
        env_ai = Path.home() / ".env.ai"
        if env_ai.exists():
            for line in env_ai.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

        api_key = os.environ.get(API_KEY_ENV, "").strip().strip('"')
        if not api_key:
            print("ERROR: Translation API credentials are not configured.")
            sys.exit(1)

    # Load English source
    en_file = I18N_DIR / "en.json"
    if not en_file.exists():
        print(f"ERROR: English source not found: {en_file}")
        sys.exit(1)

    with open(en_file, "r", encoding="utf-8") as f:
        en_data = json.load(f)

    en_keys = len(flatten(en_data))
    print("\n🌐 zTTato i18n Auto-Translator")
    print(f"   Source: en.json ({en_keys} keys)")
    print(f"   Model:  {args.model}")
    print(f"   Mode:   {'DRY RUN' if args.dry_run else 'WRITE'}")
    print()

    locales_to_process = {args.locale: TARGET_LOCALES[args.locale]} if args.locale else TARGET_LOCALES

    total_translated = 0
    for locale_code, locale_name in locales_to_process.items():
        print(f"[{locale_code}] {locale_name}")
        translated, skipped = translate_locale(
            api_key, en_data, locale_code, locale_name, dry_run=args.dry_run, model=args.model
        )
        total_translated += translated
        print()

    print(f"✅ Done. Translated {total_translated} strings across {len(locales_to_process)} locales.")
    if args.dry_run:
        print("   (Dry run — no files written. Remove --dry-run to apply.)")


if __name__ == "__main__":
    main()
