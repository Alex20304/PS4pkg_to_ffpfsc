from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping


LOGGER = logging.getLogger(__name__)

DEFAULT_LOCALE = "ru"
ENGLISH_LOCALE = "en"
LOCALES_DIRECTORY = Path(__file__).resolve().with_name("locales")
PREFERRED_LOCALE_ORDER = (DEFAULT_LOCALE, ENGLISH_LOCALE)
LOCALE_LABELS = {
    "ru": "Русский",
    "en": "English",
    "zh_CN": "简体中文",
    "zh-CN": "简体中文",
}

LocaleCatalog = dict[str, str]
LocaleCatalogs = dict[str, LocaleCatalog]


def _locale_sort_key(locale: str) -> tuple[int, str]:
    try:
        return PREFERRED_LOCALE_ORDER.index(locale), ""
    except ValueError:
        return len(PREFERRED_LOCALE_ORDER), locale.casefold()


def _read_locale_catalog(path: Path) -> LocaleCatalog | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        LOGGER.warning("ignoring unreadable locale file %s: %s", path, error)
        return None
    if not isinstance(payload, dict):
        LOGGER.warning("ignoring locale file with a non-object root: %s", path)
        return None
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        LOGGER.warning("ignoring locale file with non-string entries: %s", path)
        return None
    return dict(payload)


def load_locale_catalogs(directory: Path | None = None) -> LocaleCatalogs:
    locale_directory = directory or LOCALES_DIRECTORY
    try:
        locale_files = sorted(
            locale_directory.glob("*.json"),
            key=lambda path: _locale_sort_key(path.stem),
        )
    except OSError as error:
        LOGGER.warning(
            "unable to enumerate locale directory %s: %s",
            locale_directory,
            error,
        )
        locale_files = []

    catalogs: LocaleCatalogs = {}
    for path in locale_files:
        try:
            is_file = path.is_file()
        except OSError as error:
            LOGGER.warning("ignoring inaccessible locale path %s: %s", path, error)
            continue
        if not is_file:
            continue
        catalog = _read_locale_catalog(path)
        if catalog is not None:
            catalogs[path.stem] = catalog

    # Keep a usable fallback even when the packaged English file is missing or
    # damaged. In that exceptional case untranslated keys are shown, but the GUI
    # still starts and custom catalogs can remain usable.
    catalogs.setdefault(ENGLISH_LOCALE, {})
    return dict(sorted(catalogs.items(), key=lambda item: _locale_sort_key(item[0])))


def locale_display_name(locale: str) -> str:
    return LOCALE_LABELS.get(locale, locale)


def _match_available_locale(
    catalogs: Mapping[str, Mapping[str, str]],
    system_locale_name: str,
) -> str | None:
    available = {
        locale.replace("-", "_").casefold(): locale for locale in catalogs
    }
    normalized = system_locale_name.replace("-", "_").strip("_")
    candidates = [normalized]
    lowered = normalized.casefold()
    if lowered.startswith("zh_hans") or lowered in {"zh_cn", "zh_sg"}:
        candidates.append("zh_CN")
    if "_" in normalized:
        candidates.append(normalized.split("_", 1)[0])

    for candidate in candidates:
        match = available.get(candidate.casefold())
        if match is not None:
            return match
    return None


def system_languages_or_english(
    catalogs: Mapping[str, Mapping[str, str]],
    system_locale_names: Iterable[str],
) -> str:
    for system_locale_name in system_locale_names:
        match = _match_available_locale(catalogs, system_locale_name)
        if match is not None:
            return match
    return ENGLISH_LOCALE


def system_locale_or_english(
    catalogs: Mapping[str, Mapping[str, str]],
    system_locale_name: str,
) -> str:
    return system_languages_or_english(catalogs, (system_locale_name,))


def translate_catalog(
    catalogs: Mapping[str, Mapping[str, str]],
    language: str,
    key: str,
    **values: Any,
) -> str:
    candidates: list[tuple[str, str]] = []
    selected = catalogs.get(language)
    if isinstance(selected, Mapping):
        text = selected.get(key)
        if isinstance(text, str):
            candidates.append((language, text))

    if language != ENGLISH_LOCALE:
        fallback = catalogs.get(ENGLISH_LOCALE)
        if isinstance(fallback, Mapping):
            text = fallback.get(key)
            if isinstance(text, str):
                candidates.append((ENGLISH_LOCALE, text))

    for locale, text in candidates:
        if not values:
            return text
        try:
            return text.format(**values)
        except Exception as error:
            # A community locale must not be able to break the GUI with an
            # unmatched brace, a missing placeholder, or an invalid format spec.
            LOGGER.warning(
                "invalid translation format for locale %s key %s: %s",
                locale,
                key,
                error,
            )
    return key


TEXTS = load_locale_catalogs()


def translate(key: str, language: str = DEFAULT_LOCALE, **values: Any) -> str:
    return translate_catalog(TEXTS, language, key, **values)
