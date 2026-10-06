"""Screening PV estimate from GHI, installed DC power and performance ratio.

No claim of a PVsyst simulation: tilt, tracking and hourly shading need a POA model.
"""

import calendar

PANEL_AREA_M2 = 2.134 * 1.051
PANEL_POWER_KW = 0.445


def annual_to_monthly(annual: float, year: int) -> list[float]:
    days = [calendar.monthrange(year, m)[1] for m in range(1, 13)]
    return [annual * d / sum(days) for d in days]


def calculate_energy(area_ha: float, coverage: float, monthly: list[float], performance_ratio: float) -> dict:
    count = int(area_ha * 10000 * coverage / PANEL_AREA_M2)
    power = count * PANEL_POWER_KW
    monthly_energy = [power * radiation * performance_ratio for radiation in monthly]
    return {
        "num_panels": count,
        "total_power": power,
        "panel_area_total": count * PANEL_AREA_M2,
        "annual_energy": sum(monthly_energy),
        "monthly_energy": monthly_energy,
        "specific_yield": sum(monthly_energy) / power if power else 0,
        "total_efficiency": performance_ratio,
        "method": "GHI × DC kWp × performance ratio; screening estimate",
        "units": {"total_power": "kWp", "annual_energy": "kWh/year"},
    }
