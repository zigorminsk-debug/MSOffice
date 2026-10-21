"""Русские падежные формы для отображения направлений расчётов."""

from __future__ import annotations

import re
from functools import lru_cache

try:
    import pymorphy3
except ImportError:  # pragma: no cover — резерв для запуска без необязательного словаря
    pymorphy3 = None

_MORPH = None
_WORD = re.compile(r"[А-ЯЁа-яёA-Za-z-]+")
_INVARIABLE = {"ооо", "оао", "зао", "ип", "чуп", "уп", "пао"}


def _restore_case(source: str, result: str) -> str:
    if source.isupper():
        return result.upper()
    if source[:1].isupper():
        return result[:1].upper() + result[1:]
    return result


def _fallback(word: str) -> str:
    """Осторожный запасной вариант для частых имён, если словарь недоступен."""
    lower = word.lower()
    irregular = {"павел": "павлу", "лев": "льву", "пётр": "петру", "игорь": "игорю"}
    if lower in irregular:
        return _restore_case(word, irregular[lower])
    if lower.endswith(("ия", "ья")):
        return word[:-1] + "и"
    if lower.endswith("а"):
        return word[:-1] + "е"
    if lower.endswith("я"):
        return word[:-1] + "е"
    if lower.endswith("й"):
        return word[:-1] + "ю"
    if lower.endswith("ь"):
        return word[:-1] + "ю"
    if lower[-1:] in "бвгджзклмнпрстфхцчшщ":
        return word + "у"
    return word


@lru_cache(maxsize=1024)
def _word_to_dative(word: str) -> str:
    if word.casefold() in _INVARIABLE or (word.isupper() and len(word) <= 6):
        return word
    if pymorphy3 is None:
        return _fallback(word)
    global _MORPH
    if _MORPH is None:
        _MORPH = pymorphy3.MorphAnalyzer()
    parses = _MORPH.parse(word)
    # Имена/фамилии и существительные предпочтительнее случайного разбора.
    nominative = [item for item in parses if "nomn" in item.tag]
    parsed = next((item for item in nominative if {"Name", "Surn", "Patr"} & set(item.tag.grammemes)), nominative[0] if nominative else parses[0])
    inflected = parsed.inflect({"datv"})
    return _restore_case(word, inflected.word) if inflected else _fallback(word)


@lru_cache(maxsize=512)
def to_dative(value: str) -> str:
    """Склоняет имена и названия организаций в дательный падеж.

    Юра → Юре, Игорь → Игорю, ООО «Ромашка» → ООО «Ромашке».
    Юридические формы и аббревиатуры остаются неизменными.
    """
    text = (value or "").strip()
    if not text:
        return text
    return _WORD.sub(lambda match: _word_to_dative(match.group(0)), text)
