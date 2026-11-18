"""Speech-only Russian text normalization for Qwen-Talker and other TTS engines."""
from __future__ import annotations

import importlib.util
import re
import threading
from datetime import date
from typing import Any

_LOCK = threading.RLock()
_ACCENTIZER: Any = None
_ACCENT_ERROR: str | None = None
_NUM2WORDS: Any = None
_MORPH: Any = None
_ACCENTIZER_UNLOAD_TIMER: threading.Timer | None = None
_ACCENTIZER_IDLE_SECONDS = 180.0

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def _number_words(value: int) -> str:
    global _NUM2WORDS
    if _NUM2WORDS is None:
        from num2words import num2words

        _NUM2WORDS = num2words
    return str(_NUM2WORDS(int(value), lang="ru"))


def _plural(value: int, one: str, few: str, many: str) -> str:
    tail = abs(int(value)) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _neuter_ordinal(value: int) -> str:
    from num2words import num2words

    phrase = str(num2words(int(value), lang="ru", to="ordinal"))
    last = phrase.rsplit(" ", 1)[-1]
    if last.endswith("ий"):
        fixed = last[:-2] + "ье"
    elif last.endswith(("ый", "ой")):
        fixed = last[:-2] + "ое"
    else:
        fixed = last
    return phrase[: len(phrase) - len(last)] + fixed


def _year_genitive(value: int) -> str:
    from num2words import num2words

    phrase = str(num2words(int(value), lang="ru", to="ordinal"))
    last = phrase.rsplit(" ", 1)[-1]
    if last.endswith(("ый", "ой")):
        fixed = last[:-2] + "ого"
    elif last.endswith("ий"):
        fixed = last[:-2] + "его"
    else:
        fixed = last
    return phrase[: len(phrase) - len(last)] + fixed


def _year_inflected(value: int, case: str) -> str:
    from num2words import num2words

    phrase = str(num2words(int(value), lang="ru", to="ordinal"))
    last = phrase.rsplit(" ", 1)[-1]
    if case == "dative":
        if last.endswith(("ый", "ой")):
            fixed = last[:-2] + "ом"
        elif last.endswith("ий"):
            fixed = last[:-2] + "ем"
        else:
            fixed = last
    else:
        fixed = last
    return phrase[: len(phrase) - len(last)] + fixed


def _replace_year_with_unit(match: re.Match[str]) -> str:
    year = int(match.group("year"))
    suffix = match.group("suffix").lower()
    if not 1000 <= year <= 9999:
        return match.group(0)
    if suffix in ("году", "г."):
        words = _year_inflected(year, "dative")
    elif suffix == "года":
        words = _year_genitive(year)
    else:
        words = _year_inflected(year, "nominative")
    return f"{words} {suffix}"


def _date_words(day: int, month: int, year: int) -> str:
    try:
        date(year, month, day)
    except ValueError:
        return f"{day:02d}.{month:02d}.{year:04d}"
    return f"{_neuter_ordinal(day)} {_MONTHS[month - 1]} {_year_genitive(year)} года"


def _replace_iso_date(match: re.Match[str]) -> str:
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return _date_words(day, month, year)


def _replace_local_date(match: re.Match[str]) -> str:
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return _date_words(day, month, year)


def _replace_time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return match.group(0)
    hour_words = _number_words(hour)
    minute_words = ("ноль " if minute < 10 else "") + _number_words(minute)
    return f"{hour_words} {minute_words}"


def _replace_measure(match: re.Match[str]) -> str:
    number = match.group("number")
    unit = match.group("unit").lower()
    try:
        numeric_value = float(number.replace(",", "."))
        if "." in number or "," in number:
            global _NUM2WORDS
            if _NUM2WORDS is None:
                _NUM2WORDS = num2words
            number_words = str(_NUM2WORDS(number.replace(",", "."), lang="ru"))
            form_value = int(numeric_value)
        else:
            form_value = int(number)
            number_words = _number_words(form_value)
    except Exception:  # noqa: BLE001
        return match.group(0)

    fractional = "." in number or "," in number
    if unit in ("%", "процент", "процента", "процентов"):
        if fractional:
            unit_words = "процента"
        else:
            unit_words = _plural(form_value, "процент", "процента", "процентов")
    elif unit in ("₽", "руб", "руб.", "рубль", "рубля", "рублей"):
        if fractional:
            unit_words = "рубля"
        else:
            unit_words = _plural(form_value, "рубль", "рубля", "рублей")
    elif unit in ("км", "километр", "километра", "километров"):
        if fractional:
            unit_words = "километра"
        else:
            unit_words = _plural(form_value, "километр", "километра", "километров")
    elif unit in ("кг", "килограмм", "килограмма", "килограммов"):
        if fractional:
            unit_words = "килограмма"
        else:
            unit_words = _plural(form_value, "килограмм", "килограмма", "килограммов")
    elif unit in ("см", "сантиметр", "сантиметра", "сантиметров"):
        if fractional:
            unit_words = "сантиметра"
        else:
            unit_words = _plural(form_value, "сантиметр", "сантиметра", "сантиметров")
    elif unit in ("мм", "миллиметр", "миллиметра", "миллиметров"):
        if fractional:
            unit_words = "миллиметра"
        else:
            unit_words = _plural(form_value, "миллиметр", "миллиметра", "миллиметров")
    elif unit in ("м", "метр", "метра", "метров"):
        if fractional:
            unit_words = "метра"
        else:
            unit_words = _plural(form_value, "метр", "метра", "метров")
    elif unit in ("°c", "℃", "градус", "градуса", "градусов"):
        if fractional:
            unit_words = "градуса"
        else:
            unit_words = _plural(form_value, "градус", "градуса", "градусов")
    else:
        unit_words = unit
    return f"{number_words} {unit_words}"


def normalize_numbers(text: str) -> str:
    """Expand common Russian dates, times, quantities, percentages and numbers."""
    global _NUM2WORDS
    try:
        from num2words import num2words
    except ImportError:
        return text
    _NUM2WORDS = num2words

    value = str(text or "")
    value = re.sub(r"\bи\s+т\.\s*д\.", "и так далее", value, flags=re.IGNORECASE)
    value = re.sub(r"\bи\s+т\.\s*п\.", "и тому подобное", value, flags=re.IGNORECASE)
    value = re.sub(r"\bт\.\s*е\.", "то есть", value, flags=re.IGNORECASE)
    value = re.sub(r"\bт\.\s*к\.", "так как", value, flags=re.IGNORECASE)
    value = re.sub(r"№\s*(?=\d)", "номер ", value)
    value = re.sub(
        r"(?<!\w)(?P<year>\d{4})\s+(?P<suffix>год(?:а|у)?|г\.)(?![А-Яа-яЁё])",
        _replace_year_with_unit,
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)",
        _replace_iso_date,
        value,
    )
    value = re.sub(
        r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)",
        _replace_local_date,
        value,
    )
    value = re.sub(
        r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)",
        _replace_time,
        value,
    )

    measure = re.compile(
        r"(?P<number>(?<![\w])\d{1,12}(?:[.,]\d{1,6})?)\s*"
        r"(?P<unit>%|₽|℃|°C|процент(?:а|ов)?|руб(?:\.|ль|ля|лей)?|"
        r"километр(?:а|ов)?|км|килограмм(?:а|ов)?|кг|"
        r"сантиметр(?:а|ов)?|см|миллиметр(?:а|ов)?|мм|"
        r"метр(?:а|ов)?|м|градус(?:а|ов)?)(?![А-Яа-яЁё])",
        re.IGNORECASE,
    )
    value = measure.sub(_replace_measure, value)

    def replace_decimal(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            return str(num2words(raw.replace(" ", "").replace(",", "."), lang="ru"))
        except Exception:  # noqa: BLE001
            return raw

    value = re.sub(
        r"(?<![\w./:-])-?\d{1,3}(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)(?![\w./:-])",
        replace_decimal,
        value,
    )

    def replace_integer(match: re.Match[str]) -> str:
        global _MORPH
        raw = match.group("number")
        try:
            value_int = int(re.sub(r"[ \u00a0\u202f]", "", raw))
            if value_int > 999_999_999_999:
                return match.group(0)
            noun = match.group("noun") or ""
            gender = None
            inflected_noun = noun
            try:
                if noun:
                    if _MORPH is None:
                        from pymorphy3 import MorphAnalyzer

                        _MORPH = MorphAnalyzer()
                    parses = _MORPH.parse(noun)
                    parsed_noun = next((item for item in parses if item.tag.POS == "NOUN"), None)
                    if parsed_noun is not None:
                        gender = {"femn": "feminine", "neut": "neuter"}.get(parsed_noun.tag.gender, "masculine")
                        final_two = abs(value_int) % 100
                        final_one = abs(value_int) % 10
                        if 11 <= final_two <= 14 or final_one == 0 or final_one >= 5:
                            grammemes = {"gent", "plur"}
                        elif final_one == 1:
                            grammemes = {"nomn", "sing"}
                        else:
                            grammemes = {"gent", "sing"}
                        inflection = parsed_noun.inflect(grammemes)
                        if inflection is not None:
                            inflected_noun = inflection.word
            except Exception:  # noqa: BLE001
                pass
            number_words = str(num2words(abs(value_int), lang="ru", **({"gender": gender} if gender else {})))
            if value_int < 0:
                number_words = f"минус {number_words}"
            separator = match.group("separator") or ""
            return f"{number_words}{separator}{inflected_noun}"
        except Exception:  # noqa: BLE001
            return match.group(0)

    # Avoid changing model/version identifiers such as Qwen3 or 1.7B.
    value = re.sub(
        r"(?<![\w./:-])(?P<number>-?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,12}))"
        r"(?P<separator>\s*)(?P<noun>[А-Яа-яЁё]+)?(?![\w/:-]|\.\d)",
        replace_integer,
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _load_accentizer() -> Any:
    global _ACCENTIZER, _ACCENT_ERROR
    if _ACCENTIZER is not None:
        return _ACCENTIZER
    with _LOCK:
        if _ACCENTIZER is not None:
            return _ACCENTIZER
        try:
            from ruaccent import RUAccent
            import os
            from pathlib import Path

            public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
            model_dir = public / "Quanta" / "models" / "ruaccent"
            model_dir.mkdir(parents=True, exist_ok=True)
            accentizer = RUAccent()
            accentizer.load(
                omograph_model_size="tiny2.1",
                use_dictionary=False,
                custom_dict={
                    "кванта": "кв+анта",
                    "кванты": "кв+анты",
                    "кванте": "кв+анте",
                    "кванту": "кв+анту",
                },
                device="CPU",
                workdir=str(model_dir),
                tiny_mode=True,
            )
            _ACCENTIZER = accentizer
            _ACCENT_ERROR = None
            return accentizer
        except Exception as exc:  # noqa: BLE001
            _ACCENT_ERROR = str(exc)
            raise


def stress_status() -> dict[str, Any]:
    return {
        "ready": importlib.util.find_spec("ruaccent") is not None,
        "loaded": _ACCENTIZER is not None,
        "unloads_after_idle_seconds": _ACCENTIZER_IDLE_SECONDS,
        "error": _ACCENT_ERROR,
    }


def _unload_accentizer(expected_timer: threading.Timer | None = None) -> None:
    global _ACCENTIZER, _ACCENTIZER_UNLOAD_TIMER
    with _LOCK:
        timer = _ACCENTIZER_UNLOAD_TIMER
        if expected_timer is not None and timer is not expected_timer:
            return
        _ACCENTIZER = None
        _ACCENTIZER_UNLOAD_TIMER = None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()
    import gc

    gc.collect()


def _schedule_accentizer_unload() -> None:
    global _ACCENTIZER_UNLOAD_TIMER
    with _LOCK:
        if _ACCENTIZER_UNLOAD_TIMER is not None:
            _ACCENTIZER_UNLOAD_TIMER.cancel()

        def unload_if_current() -> None:
            _unload_accentizer(expected_timer=timer)

        timer = threading.Timer(_ACCENTIZER_IDLE_SECONDS, unload_if_current)
        timer.name = "quanta-ruaccent-idle-unload"
        timer.daemon = True
        _ACCENTIZER_UNLOAD_TIMER = timer
        timer.start()


def normalize_for_speech(
    text: str,
    *,
    numbers: bool = True,
    stress: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Prepare only the speech copy; keep the visible chat response unchanged."""
    value = normalize_numbers(text) if numbers else str(text or "")
    status: dict[str, Any] = {"numbers": bool(numbers), "stress": False, "stress_error": None}
    if not stress or not re.search(r"[А-Яа-яЁё]{2,}", value):
        return value, status
    try:
        with _LOCK:
            accentizer = _load_accentizer()
            value = accentizer.process_all(value)
            _schedule_accentizer_unload()
        status["stress"] = True
    except Exception as exc:  # noqa: BLE001
        status["stress_error"] = str(exc)
    return value, status
