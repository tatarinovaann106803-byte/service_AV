"""Scenario water balance and separate probability/severity loss assessment."""

from .schemas import Risks, Water


def water_balance(w: Water, area_ha: float) -> dict:
    soil_saving = w.soil_evaporation_mm * w.soil_evaporation_reduction_pct / 100
    et_av = w.baseline_et_mm - soil_saving
    rainfall_av = w.effective_rainfall_mm if w.effective_rainfall_av_mm is None else w.effective_rainfall_av_mm
    baseline = (
        max(0, w.baseline_et_mm - w.effective_rainfall_mm - w.available_soil_water_mm) * 10 / w.irrigation_efficiency
    )
    av = max(0, et_av - rainfall_av - w.available_soil_water_mm) * 10 / w.irrigation_efficiency
    saved = (baseline - av) * area_ha
    return {
        "status": "user_scenario",
        "baseline_irrigation_m3_ha": baseline,
        "av_irrigation_m3_ha": av,
        "saved_m3_year": saved,
        "irrigation_reduction_pct": (baseline - av) / baseline * 100 if baseline else None,
        "soil_evaporation_saved_mm": soil_saving,
        "et_av_mm": et_av,
        "annual_savings_rub": saved * w.water_cost_rub_m3,
        "assumption": "Одинаковые транспирация и доступная почвенная влага; сезонный баланс, не график полива",
    }


def crop_risks(risks: Risks, baseline_revenue: float, av_revenue: float) -> dict:
    perils = {}
    for key in ("frost", "drought", "hail"):
        h = getattr(risks, key)
        p0, s0 = h.probability_pct / 100, h.loss_fraction_pct / 100
        p1 = p0 * (1 - h.probability_reduction_pct / 100)
        s1 = s0 * (1 - h.severity_reduction_pct / 100)
        loss0, loss1 = baseline_revenue * p0 * s0, av_revenue * p1 * s1
        perils[key] = {
            "baseline_probability_pct": p0 * 100,
            "av_probability_pct": p1 * 100,
            "probability_reduction_pp": (p0 - p1) * 100,
            "relative_probability_reduction_pct": h.probability_reduction_pct,
            "baseline_expected_loss_rub": loss0,
            "av_expected_loss_rub": loss1,
            "avoided_expected_loss_rub": loss0 - loss1,
        }
    total = (
        sum(p["avoided_expected_loss_rub"] for p in perils.values()) if risks.events_are_mutually_exclusive else None
    )
    return {
        "status": "user_scenario",
        "perils": perils,
        "total_avoided_loss_rub": total,
        "cash_flow_adjustment_rub": total if risks.include_in_cash_flow else 0,
        "included_in_cash_flow": risks.include_in_cash_flow,
        "note": "Энерговыручка не снижает физическую тяжесть потери урожая; страховая премия не рассчитывается",
    }
