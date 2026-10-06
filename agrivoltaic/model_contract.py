"""Shared training/inference contract; no silently reordered features."""

import hashlib
from dataclasses import dataclass

FEATURES = [
    "coverage",
    "height",
    "air_temperature_open_c",
    "relative_humidity_open_pct",
    "ghi_kwh_m2_day",
    "wind_speed_m_s",
    "soil_moisture_open_pct",
]
TARGETS = {"air_temperature_delta_c": "degC", "soil_temperature_delta_c": "degC", "et_reduction_pct": "%"}
BOUNDS = {"air_temperature_delta_c": (-30, 30), "soil_temperature_delta_c": (-40, 40), "et_reduction_pct": (-100, 100)}
FEATURE_BOUNDS = [(0, 1), (0.01, 50), (-60, 60), (0, 100), (0, 15), (0, 70), (0, 100)]
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ModelSpec:
    name: str
    features: list[str]
    targets: dict[str, str]
    feature_bounds: list[tuple[float, float]]
    target_bounds: dict[str, tuple[float, float]]
    crop_name: str | None = None


MICROCLIMATE = ModelSpec("microclimate", FEATURES, TARGETS, FEATURE_BOUNDS, BOUNDS)


def yield_spec(crop: str) -> ModelSpec:
    # One model per named crop; never pool wheat and lettuce as interchangeable.
    key = hashlib.sha256(crop.encode("utf-8")).hexdigest()[:16]
    return ModelSpec(
        f"yield_{key}",
        ["coverage", "height", "growing_season_temperature_c", "growing_season_rainfall_mm", "irrigation_mm"],
        {"relative_yield_pct": "% of control"},
        [(0, 1), (0.01, 50), (-30, 55), (0, 10000), (0, 10000)],
        {"relative_yield_pct": (0, 500)},
        crop_name=crop,
    )
