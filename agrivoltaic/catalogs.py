"""Local reference data, with explicit units, dates and provenance limitations."""

import csv
import hashlib
import math
from functools import lru_cache
from pathlib import Path

from .config import DATA

CROPS = {
    "Пшеница": "Wheat",
    "Кукуруза": "Maize (corn)",
    "Соя": "Soya beans",
    "Подсолнечник": "Sunflower seed",
    "Картофель": "Potatoes",
    "Сахарная свекла": "Sugar beet",
}
CATALOGS = {
    "crop_prices.csv": {"region", "crop", "price_rub_per_kg"},
    "crops_yield_faostat.csv": {"Area", "Item", "Element", "Year", "Unit", "Value"},
    "aquaculture_production.csv": {"Country (Name)", "ASFIS species (Name)", "Unit (Name)"},
    "aquaculture_value.csv": {"Country (Name)", "ASFIS species (Name)", "Unit (Name)"},
    "forestry_production.csv": {"Area", "Item", "Element", "Year", "Unit", "Value"},
    "forestry_trade.csv": {"Area", "Item", "Element", "Year", "Unit", "Value"},
}


@lru_cache(maxsize=12)
def read_catalog(name: str, directory: Path = DATA) -> list[dict]:
    with (directory / name).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not CATALOGS[name].issubset(reader.fieldnames or []):
            raise ValueError(f"Некорректные колонки справочника {name}")
        return list(reader)


def valid_number(value: str) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def crop_price(name: str, region: str | None) -> tuple[float, dict]:
    if not region:
        raise ValueError("Укажите crop_price вручную или region для поиска цены")
    rows = [r for r in read_catalog("crop_prices.csv") if r["crop"] == name and r["region"] == region]
    values = [valid_number(r["price_rub_per_kg"]) for r in rows]
    if len(values) != 1 or values[0] is None:
        raise ValueError("Однозначная цена не найдена; укажите crop_price в руб/кг")
    return values[0], {
        "source": "data/crop_prices.csv",
        "unit": "RUB/kg",
        "year": None,
        "provenance": "unverified",
        "region": region,
        "warning": "В файле нет даты и первичной ссылки Росстата; проверьте цену вручную",
    }


def crop_yield(name: str, country: str) -> tuple[float, dict]:
    english = CROPS.get(name, name)
    rows = [
        r
        for r in read_catalog("crops_yield_faostat.csv")
        if r["Area"] == country
        and r["Item"] == english
        and r["Element"] == "Yield"
        and valid_number(r["Value"]) is not None
    ]
    if not rows:
        raise ValueError("Урожайность не найдена; укажите base_yield в кг/га/год")
    year = max(int(r["Year"]) for r in rows)
    rows = [r for r in rows if int(r["Year"]) == year]
    if len(rows) != 1:
        raise ValueError("Неоднозначная урожайность; укажите base_yield")
    row = rows[0]
    factors = {"kg/ha": 1, "hg/ha": 0.1, "100 mg/ha": 0.0001, "t/ha": 1000}
    if row["Unit"] not in factors:
        raise ValueError(f"Неподдерживаемая единица урожайности: {row['Unit']}")
    return float(row["Value"]) * factors[row["Unit"]], {
        "source": "data/crops_yield_faostat.csv",
        "unit": "kg/ha/year",
        "year": year,
        "country": country,
        "item": english,
        "original_unit": row["Unit"],
        "provenance": "local_snapshot_unverified_origin",
        "warning": "Средняя урожайность по стране; не измерение на выбранном участке",
    }


def catalog_status() -> list[dict]:
    result = []
    for name in CATALOGS:
        try:
            rows = read_catalog(name)
            result.append(
                {
                    "file": name,
                    "status": "loaded",
                    "rows": len(rows),
                    "sha256": hashlib.sha256((DATA / name).read_bytes()).hexdigest(),
                    "remote_origin_verified": False,
                }
            )
        except (OSError, ValueError, csv.Error) as exc:
            result.append({"file": name, "status": "error", "error": str(exc)})
    return result
