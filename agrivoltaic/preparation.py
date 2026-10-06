"""Conservative extraction: missing/ambiguous measurements remain missing."""

import re

import pandas as pd

from .config import DATASET
from .ingestion import atomic_json, current_raw


def number(value, percent=False):
    text = str(value).strip().replace("−", "-").replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*%?", text):
        return None
    result = float(text.replace("%", "").strip())
    return result / 100 if percent and "%" in text else result


def prepare_dataset():
    frame = pd.read_csv(current_raw(), dtype=str, keep_default_na=False)
    destination = DATASET / "observations"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"rows": len(frame), "sectors": {}, "note": "Извлечённые данные требуют проверки по первоисточникам"}
    for sector, organism in [
        ("crop_farming", "Культура"),
        ("forestry", "Woody_Species"),
        ("aquaculture", "Aquatic organism"),
    ]:
        subset = frame[frame[organism].str.strip().ne("")].copy()
        # Preserve originals and lineage instead of inventing lat/lon or physiological features.
        subset.to_csv(destination / f"{sector}.csv", index=False)
        report["sectors"][sector] = len(subset)
    crops = frame[frame["Культура"].str.strip().ne("")]
    records = []
    for index, row in crops.iterrows():
        records.append(
            {
                "study_id": row.get("DOI", ""),
                "source_row": row.get("source_row", index + 2),
                "crop": row["Культура"],
                "reviewed": False,
                "coverage": number(row.get("Покрытие поверхности земли по вертикали", ""), percent=True),
                "height": number(row.get("Высота над землей (м)", "")),
                "air_temperature_delta_c": number(row.get("Изменение температуры воздуха (гр.)", "")),
                "soil_temperature_delta_c": number(row.get("Изменение температуры почвы (гр.)", "")),
                "et_reduction_pct": number(row.get("Снижение эвапотранспирации", "")),
            }
        )
    extracted = pd.DataFrame(records)
    extracted.to_csv(destination / "microclimate_unreviewed.csv", index=False)
    targets = ["air_temperature_delta_c", "soil_temperature_delta_c", "et_reduction_pct"]
    report["microclimate_nonmissing"] = {key: int(extracted[key].notna().sum()) for key in targets}
    report["microclimate_complete_targets"] = int(extracted[targets].notna().all(axis=1).sum())
    report["training_ready"] = False
    yield_rows = []
    for index, row in crops.iterrows():
        raw_yield = row.get("Урожайность", "").strip()
        yield_rows.append(
            {
                "study_id": row.get("DOI", ""),
                "crop_name": row["Культура"],
                "source_row": row.get("source_row", index + 2),
                "reviewed": False,
                "relative_yield_pct": number(raw_yield) if raw_yield.endswith("%") else None,
                "coverage": number(row.get("Покрытие поверхности земли по вертикали", ""), percent=True),
                "height": number(row.get("Высота над землей (м)", "")),
            }
        )
    yield_frame = pd.DataFrame(yield_rows)
    yield_frame.to_csv(destination / "yield_unreviewed.csv", index=False)
    complete = yield_frame.dropna(subset=["relative_yield_pct", "coverage", "height"])
    report["yield_nonmissing"] = int(yield_frame["relative_yield_pct"].notna().sum())
    report["yield_with_geometry"] = len(complete)
    report["yield_with_geometry_publications"] = int(complete["study_id"].nunique())
    report["yield_counts_by_crop"] = {
        k: int(v) for k, v in yield_frame.groupby("crop_name")["relative_yield_pct"].count().items()
    }
    atomic_json(DATASET / "preparation_report.json", report)
    return report
