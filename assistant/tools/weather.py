# ruff: noqa: F403,F405
"""Tool implementations grouped by weather domain."""

from .common import *  # noqa: F403
from .decorators import tool


class WeatherToolsMixin:
    @staticmethod
    def _condition_code(condition: str) -> int:
        value = (condition or "").lower()
        if "гроза" in value:
            return 95
        if "снег" in value or "метел" in value:
            return 71
        if "ливень" in value:
            return 80
        if "дождь" in value:
            return 61
        if "морос" in value:
            return 51
        if "туман" in value or "дымка" in value:
            return 45
        if "пасмур" in value or "облач" in value:
            return 3
        if "ясно" in value or "солнеч" in value:
            return 0
        return -1

    def _yandex_weather(self, city: str) -> dict[str, Any]:
        """Yandex is a reliable fallback when Open-Meteo is unreachable."""
        slug = "krasnodar" if city.lower().replace("ё", "е") == "краснодар" else urllib.parse.quote(city.lower().replace(" ", "-"))
        page_raw, _content_type, _final_url = _safe_get(
            "https://yandex.ru/pogoda/" + slug, timeout=9,
            headers={"Accept": "text/html"}, max_bytes=2_000_000,
        )
        page = page_raw.decode("utf-8", "replace")
        hidden = re.search(r"погода сейчас:\s*(.*?)</p>", page, re.S)
        if not hidden:
            raise ValueError("Яндекс не отдал текущую погоду")
        text = re.sub(r"<[^>]+>", " ", hidden.group(1))
        text = html.unescape(text)
        temp_match = re.search(r"Температура воздуха\s*([+-]?\d+)°", text)
        if not temp_match:
            raise ValueError("не найдена температура")
        feels_match = re.search(r"ощущается как\s*([+-]?\d+)°", text)
        wind_match = re.search(r"Скорость ветра\s*([\d.,]+)\s*м/с", text)
        humidity_match = re.search(r"Влажность\s*(\d+)%", text)
        condition_match = re.search(r"^\s*([^.]+)\.", text)
        condition = (condition_match.group(1).strip() if condition_match else "—").strip(" ,")
        condition = re.sub(r"\s+", " ", condition)
        day_match = re.search(r"Днём.*?AppForecastDayPart_temp[^>]*>\s*([+-]?\d+)°", page, re.S)
        night_match = re.search(r"Ночью.*?AppForecastDayPart_temp[^>]*>\s*([+-]?\d+)°", page, re.S)
        temp = int(temp_match.group(1))
        feels = int(feels_match.group(1)) if feels_match else None
        wind = wind_match.group(1).replace(",", ".") if wind_match else None
        humidity = int(humidity_match.group(1)) if humidity_match else None
        return {
            "ok": True,
            "city": city,
            "temp": temp,
            "feels": feels,
            "wind": wind,
            "wind_unit": "м/с",
            "humidity": humidity,
            "code": self._condition_code(condition),
            "condition": condition,
            "min": int(night_match.group(1)) if night_match else None,
            "max": int(day_match.group(1)) if day_match else None,
            "source": "Яндекс.Погода",
        }

    def _open_meteo_weather(self, city: str) -> dict[str, Any]:
        settings = self.store.settings
        lat = float(settings.get("weather_lat", 45.0355))
        lon = float(settings.get("weather_lon", 38.9753))
        if city.lower() != str(settings.get("weather_city", "")).lower():
            try:
                geo = _http_json(
                    "https://geocoding-api.open-meteo.com/v1/search?name="
                    + urllib.parse.quote(city) + "&count=1&language=ru&format=json",
                    timeout=6,
                )
                item = (geo.get("results") or [None])[0]
                if item:
                    lat, lon = item["latitude"], item["longitude"]
            except Exception:
                pass
        url = (
            "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m"
            "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1"
        ).format(lat=lat, lon=lon)
        data = _http_json(url, timeout=6)
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        code = int(current.get("weather_code", -1))
        return {
            "ok": True,
            "city": city,
            "temp": current.get("temperature_2m"),
            "feels": current.get("apparent_temperature"),
            "wind": current.get("wind_speed_10m"),
            "wind_unit": "км/ч",
            "humidity": current.get("relative_humidity_2m"),
            "code": code,
            "condition": self.WEATHER_CODES.get(code, "неизвестно"),
            "min": (daily.get("temperature_2m_min") or [None])[0],
            "max": (daily.get("temperature_2m_max") or [None])[0],
            "source": "Open-Meteo",
        }

    def weather_info(self, city: str = "") -> dict[str, Any]:
        settings = self.store.settings
        city = (city or settings.get("weather_city") or "Краснодар").strip()
        key = city.lower()
        cached = self._weather_cache.get(key)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        errors: list[str] = []
        result: dict[str, Any] | None = None
        for loader in (self._yandex_weather, self._open_meteo_weather):
            try:
                result = loader(city)
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{loader.__name__.replace('_', ' ')}: {exc}")
        if not result:
            return {"ok": False, "text": "Погода недоступна: " + "; ".join(errors[-2:])}
        parts = [f"Погода в городе {city}: {result.get('condition') or '—'}, {result.get('temp')}°C"]
        if result.get("feels") is not None:
            parts.append(f"ощущается как {result['feels']}°C")
        if result.get("wind") is not None:
            parts.append(f"ветер {result['wind']} {result.get('wind_unit', 'км/ч')}")
        if result.get("humidity") is not None:
            parts.append(f"влажность {result['humidity']}%")
        if result.get("min") is not None and result.get("max") is not None:
            parts.append(f"днём от {result['min']}°C до {result['max']}°C")
        text = ", ".join(parts) + "."
        if result.get("source"):
            text += f" Источник: {result['source']}."
        result["text"] = text
        result["ok"] = True
        self._weather_cache[key] = (time.time(), result)
        return result

    @tool(
        name='weather', label='Погода',
        risk='read', confirm=False,
        capabilities=('сеть_наружу',),
        description="Get current weather and today's forecast. Defaults to the user's city (Краснодар).",
        parameters=schema(city=prop('string', 'City name. Empty means the configured city.', default='')),
    )
    def _weather_tool(self, city: str = "") -> dict[str, Any]:
        return self.weather_info(city)
