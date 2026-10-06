"""Arithmetic row means per product; keep relative and absolute observations apart."""

from collections import defaultdict
from statistics import mean

import pandas as pd

from .ingestion import current_raw
from .preparation import number

ALIASES = {
    "crop": {"томаты": "томат", "томат solanum lycopersicon": "томат", "салат": "салат-латук"},
    "forest": {"poplar": "тополь"},
    "aqua": {
        "pacific white shrimp litopenaeus vannamei": "белоногая креветка",
        "litopenaeus vannamei (pacific white shrimp)": "белоногая креветка",
        "salmo salar (atlantic salmon)": "атлантический лосось",
        "apostichopus japonicus (sea cucumber)": "японский трепанг",
    },
}
LABELS = {
    "тополь": "Тополь",
    "белоногая креветка": "Белоногая креветка",
    "атлантический лосось": "Атлантический лосось",
    "японский трепанг": "Японский трепанг",
    "томат": "Томат",
}
COLUMNS = {
    "crop": ("Культура", "Урожайность", "relative_percent"),
    "forest": ("Woody_Species", "Woody_Relative_Yield_vs_Monoculture_%", "relative_percent"),
    "aqua": ("Aquatic organism", "Aquaculture production (t/ha)", "absolute_t_ha"),
}
# Broad reviews and mixed species are not individual species observations.
AQUA_GENERIC = {"fish", "forest", "microalgae"}


def normalize(sector, value):
    key = " ".join(str(value).strip().lower().split())
    return ALIASES.get(sector, {}).get(key, key)


def observations():
    frame = pd.read_csv(current_raw(), dtype=str, keep_default_na=False)
    groups = defaultdict(list)
    labels = {}
    for sector, (name_col, value_col, kind) in COLUMNS.items():
        for index, row in frame.iterrows():
            name = row.get(name_col, "").strip()
            raw = row.get(value_col, "").strip()
            value = number(raw)
            if not name or value is None or value < 0:
                continue
            if kind == "relative_percent" and (not raw.endswith("%") or value > 500):
                continue
            key = normalize(sector, name)
            if sector == "aqua" and (key in AQUA_GENERIC or "," in name):
                continue
            groups[(sector, key)].append(
                {
                    "value": value,
                    "doi": row.get("DOI", ""),
                    "row": row.get("source_row", str(index + 2)),
                    "source_id": row.get("source_id", sector),
                }
            )
            labels[(sector, key)] = LABELS.get(key, name)
    return groups, labels


def product_catalog():
    frame = pd.read_csv(current_raw(), dtype=str, keep_default_na=False)
    result = {}
    for sector, (name_column, value_column, kind) in COLUMNS.items():
        names = set()
        for _, row in frame.iterrows():
            name, raw = row[name_column].strip(), row[value_column].strip()
            value = number(raw)
            if not name or value is None or value < 0:
                continue
            if kind == "relative_percent" and (not raw.endswith("%") or value > 500):
                continue
            if sector == "aqua" and (normalize(sector, name) in AQUA_GENERIC or "," in name):
                continue
            names.add(name)
        result[sector] = [{"name": name} for name in sorted(names)]
    return result


def coefficient(sector, product, baseline):
    groups, _ = observations()
    rows = groups.get((sector, normalize(sector, product)), [])
    if not rows:
        return {
            "factor": 1.0,
            "rows": 0,
            "method": "neutral_no_observations",
            "message": "Для выбранного вида нет сопоставимых числовых данных; используется нейтральный исходный ориентир с расчётной поправкой на условия.",
            "lineage": [],
        }
    average = mean(row["value"] for row in rows)
    kind = COLUMNS[sector][2]
    if kind == "relative_percent":
        factor = average / 100
        message = f"Средний коэффициент по {len(rows)} строкам базы для выбранной культуры или породы."
    else:
        if baseline <= 0:
            raise ValueError("Для сравнения продуктивности аквакультуры задайте базовую продуктивность больше нуля")
        factor = average / baseline
        message = (
            f"Средняя продуктивность по {len(rows)} строкам базы — {average:g} т/га; "
            "сравнение с указанной вами базовой продуктивностью, а не установленный эффект панелей."
        )
    return {
        "factor": factor,
        "rows": len(rows),
        "mean": average,
        "method": kind,
        "min": min(r["value"] for r in rows),
        "max": max(r["value"] for r in rows),
        "publications": len({r["doi"] for r in rows if r["doi"]}),
        "message": message,
        "lineage": rows,
    }
