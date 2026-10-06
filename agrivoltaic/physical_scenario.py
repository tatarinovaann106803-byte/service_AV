"""Geometry → light distribution → scenario microclimate → productivity.

Representative-day ray geometry and an explicit, uncalibrated response model.
Parameters are screening assumptions, not fitted biological constants.
"""

import calendar
from dataclasses import dataclass

import numpy as np

from .energy import PANEL_AREA_M2, PANEL_POWER_KW

ROW_WIDTH = 2.134
MODULE_WIDTH = 1.051


@dataclass(frozen=True)
class Environment:
    latitude: float
    monthly_ghi: tuple[float, ...]
    air_temperature_c: float = 22
    rainfall_mm: float = 300
    irrigation_mm: float = 250
    et_demand_mm: float = 500
    year: int = 2024
    soil_evaporation_fraction: float = 0.3


def illumination(pitch, height, tilt_deg, env: Environment, diffuse_fraction=0.35):
    tilt = np.radians(tilt_deg)
    latitude = np.radians(env.latitude)
    orientation = 1 if env.latitude >= 0 else -1
    hours = np.arange(-5, 5.01, 1)
    hour_angle = np.radians(hours * 15)
    points = (np.arange(64) + 0.5) / 64 * pitch
    crop_radiation = np.zeros(64)
    poa_monthly = []
    baseline_total = sum(env.monthly_ghi)
    for month, ghi in enumerate(env.monthly_ghi, 1):
        day = sum(calendar.monthrange(env.year, m)[1] for m in range(1, month)) + 15
        declination = np.radians(23.45 * np.sin(2 * np.pi * (284 + day) / 365.25))
        up = np.sin(latitude) * np.sin(declination) + np.cos(latitude) * np.cos(declination) * np.cos(hour_angle)
        south = np.sin(latitude) * np.cos(declination) * np.cos(hour_angle) - np.cos(latitude) * np.sin(declination)
        daylight = up > 0.05
        if not daylight.any():
            poa_monthly.append(ghi * (1 + np.cos(tilt)) / 2)
            crop_radiation += ghi * (1 - ROW_WIDTH / pitch)
            continue
        up, south = up[daylight], south[daylight]
        weights = up / up.sum()
        k = south / up
        shadow_span = ROW_WIDTH * np.abs(np.cos(tilt) + orientation * np.sin(tilt) * k)
        shadow_center = -height * k
        phase = (points[None, :] - (shadow_center - shadow_span / 2)[:, None]) % pitch
        blocked = (phase < shadow_span[:, None]) | (shadow_span[:, None] >= pitch)
        direct_by_point = (weights[:, None] * (~blocked)).sum(axis=0)
        # Isotropic diffuse screening approximation; height changes spatial shade
        # distribution, not an arbitrary multiplier of area-average irradiance.
        diffuse_transmission = max(0, 1 - ROW_WIDTH * np.cos(tilt) / pitch)
        crop_radiation += ghi * ((1 - diffuse_fraction) * direct_by_point + diffuse_fraction * diffuse_transmission)
        incidence = np.maximum(up * np.cos(tilt) + orientation * south * np.sin(tilt), 0)
        beam_ratio = np.sum(weights * incidence / up * np.minimum(1, pitch / np.maximum(shadow_span, 1e-6)))
        poa_ratio = (
            (1 - diffuse_fraction) * beam_ratio
            + diffuse_fraction * (1 + np.cos(tilt)) / 2
            + 0.2 * (1 - np.cos(tilt)) / 2
        )
        poa_monthly.append(float(ghi * poa_ratio))
    ratios = crop_radiation / baseline_total if baseline_total else np.ones(64)
    return {
        "par_ratio": float(ratios.mean()),
        "par_spatial_cv": float(ratios.std() / max(ratios.mean(), 1e-6)),
        "light_response": float(np.sqrt(np.clip(ratios, 0, 1)).mean()),
        "poa_monthly": poa_monthly,
    }


def mechanism(light, height, env, sector):
    shade = max(0, 1 - light["par_ratio"])
    cooling = 3 * shade * 3 / (height + 1.5)
    et_reduction = env.soil_evaporation_fraction * min(0.8, 0.8 * shade)
    et_av = env.et_demand_mm * (1 - et_reduction)
    heat0 = max(env.air_temperature_c - 25, 0)
    heat1 = max(env.air_temperature_c - cooling - 25, 0)
    heat_factor = np.exp(-0.02 * (heat1**2 - heat0**2))
    supply = env.rainfall_mm + env.irrigation_mm
    water0 = min(1, supply / max(env.et_demand_mm, 1))
    water1 = min(1, supply / max(et_av, 1))
    moisture_factor = (0.5 + 0.5 * water1) / (0.5 + 0.5 * water0)
    if sector == "aqua":
        # Fish are not photosynthetic: only a small scenario food-web penalty.
        light_factor = 1 - 0.15 * shade
        moisture_factor = 1
        cooling *= 0.5
        heat1 = max(env.air_temperature_c - cooling - 25, 0)
        heat_factor = np.exp(-0.02 * (heat1**2 - heat0**2))
    else:
        light_factor = light["light_response"]
    return {
        "raw_factor": float(light_factor * heat_factor * moisture_factor),
        "shade_fraction": shade,
        "air_or_water_cooling_c": cooling,
        "et_reduction_fraction": et_reduction,
        "et_av_mm": et_av,
    }


def evaluate_geometry(
    variables, env, sector, empirical_factor, reference, area_ha, calibration_multiplier=1.0, diffuse_fraction=0.35
):
    pitch, height, tilt = map(float, variables)
    light = illumination(pitch, height, tilt, env, diffuse_fraction)
    response = mechanism(light, height, env, sector)
    # Anchor the geometry response to the product's row mean at a stated reference
    # design. The correction vanishes as shade vanishes; it is not causal inference.
    correction = empirical_factor - reference["raw_factor"]
    factor = response["raw_factor"] + calibration_multiplier * correction * response["shade_fraction"] / max(
        reference["shade_fraction"], 1e-6
    )
    factor = max(0, float(factor))
    panels = int(area_ha * 10000 / (pitch * MODULE_WIDTH))
    power = panels * PANEL_POWER_KW
    monthly = [power * value * 0.85 for value in light["poa_monthly"]]
    return {
        "configuration": {
            "pitch_m": pitch,
            "height_m": height,
            "tilt_deg": tilt,
            "gcr": ROW_WIDTH / pitch,
            "projected_coverage": ROW_WIDTH * np.cos(np.radians(tilt)) / pitch,
            "num_panels": panels,
        },
        "light": light,
        "microclimate": response,
        "productivity_factor": factor,
        "energy": {
            "total_power": power,
            "annual_energy": sum(monthly),
            "monthly_energy": monthly,
            "num_panels": panels,
            "panel_area_total": panels * PANEL_AREA_M2,
        },
    }
