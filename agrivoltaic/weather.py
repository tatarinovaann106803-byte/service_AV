"""NASA POWER monthly data. Never substitute invented weather on failure."""

import calendar
import math
from datetime import datetime, timezone
from functools import lru_cache

import requests

NASA_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


class WeatherUnavailable(RuntimeError):
    pass


def parse_monthly(payload: dict, year: int) -> dict:
    try:
        params = payload["properties"]["parameter"]
        unit = payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"]
        units = unit.lower().replace(" ", "").replace("²", "^2")
        if units in {"kw-hr/m^2/day", "kwh/m^2/day", "kwh/m2/day"}:
            factor = 1.0
        elif units in {"mj/m^2/day", "mj/m2/day"}:
            factor = 1 / 3.6
        else:
            raise WeatherUnavailable(f"Неизвестные единицы NASA POWER: {unit}")
        temp_unit = payload["parameters"]["T2M"]["units"].lower()
        if temp_unit not in {"c", "degc", "°c"}:
            raise WeatherUnavailable(f"Неизвестные единицы температуры: {temp_unit}")
        fill = payload.get("header", {}).get("fill_value", -999)
        rain_unit = payload["parameters"]["PRECTOTCORR"]["units"].lower().replace(" ", "")
        if rain_unit not in {"mm/day", "mm/d"}:
            raise WeatherUnavailable("Неизвестные единицы осадков NASA POWER")
        monthly, temperatures, rainfall, days = [], [], [], []
        for month in range(1, 13):
            key = f"{year}{month:02d}"
            radiation, temperature = params["ALLSKY_SFC_SW_DWN"][key], params["T2M"][key]
            precipitation = params["PRECTOTCORR"][key]
            if any(
                v is None or v == fill or not math.isfinite(float(v)) for v in (radiation, temperature, precipitation)
            ):
                raise WeatherUnavailable(f"Пропуск NASA POWER: {key}")
            if float(precipitation) < 0 or float(radiation) < 0 or not -90 <= float(temperature) <= 70:
                raise WeatherUnavailable(f"Недопустимое значение NASA POWER: {key}")
            n_days = calendar.monthrange(year, month)[1]
            days.append(n_days)
            monthly.append(float(radiation) * factor * n_days)
            temperatures.append(float(temperature))
            rainfall.append(float(precipitation) * n_days)
        return {
            "annual": sum(monthly),
            "monthly": monthly,
            "temperature": sum(t * d for t, d in zip(temperatures, days)) / sum(days),
            "temperature_monthly": temperatures,
            "precipitation_monthly_mm": rainfall,
            "precipitation_annual_mm": sum(rainfall),
            "year": year,
            "source": "NASA POWER",
            "original_radiation_unit": unit,
            "radiation_unit": "kWh/m2/year",
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherUnavailable("Некорректный ответ NASA POWER") from exc


@lru_cache(maxsize=256)
def fetch_weather(lat: float, lon: float, year: int) -> dict:
    if year >= datetime.now(timezone.utc).year:
        raise WeatherUnavailable("Для годового расчёта выберите завершённый календарный год")
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M,PRECTOTCORR",
        "community": "RE",
        "latitude": lat,
        "longitude": lon,
        "start": year,
        "end": year,
        "format": "JSON",
    }
    try:
        response = requests.get(NASA_URL, params=params, timeout=(5, 25))
        response.raise_for_status()
        result = parse_monthly(response.json(), year)
    except (requests.RequestException, ValueError) as exc:
        raise WeatherUnavailable("NASA POWER недоступен. Повторите запрос или введите погодные данные вручную") from exc
    result["url"] = response.url
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    return result


def season_weather(weather, start_month, end_month):
    months = (
        list(range(start_month, end_month + 1))
        if start_month <= end_month
        else list(range(start_month, 13)) + list(range(1, end_month + 1))
    )
    days = [calendar.monthrange(weather["year"], month)[1] for month in months]
    return {
        "radiation_annual": weather["annual"],
        "growing_season_temperature_c": sum(weather["temperature_monthly"][m - 1] * d for m, d in zip(months, days))
        / sum(days),
        "growing_season_rainfall_mm": sum(weather["precipitation_monthly_mm"][m - 1] for m in months),
    }
