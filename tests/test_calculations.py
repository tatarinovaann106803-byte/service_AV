import math

import pytest
from pydantic import ValidationError

from agrivoltaic.agronomy import crop_risks, water_balance
from agrivoltaic.economics import calculate_economics, crossing_year, irr
from agrivoltaic.energy import annual_to_monthly, calculate_energy
from agrivoltaic.schemas import CalculationRequest, Capital, Hazard, Risks, Water


def request(**updates):
    return CalculationRequest(
        **{
            "lat": 45,
            "lon": 38,
            "area_ha": 10,
            "coverage": 0.3,
            "height": 3,
            "energy_price": 1,
            "crop_price": 15,
            "base_yield": 4000,
            "radiation_annual": 1400,
            **updates,
        }
    )


def test_wacc():
    capital = Capital(method="wacc", equity_share_pct=60, cost_of_equity_pct=15, cost_of_debt_pct=12, tax_rate_pct=25)
    assert capital.rate() == pytest.approx(0.126)


def test_analytical_npv_and_payback():
    r = request(project_years=2, capex_per_kw=100, degradation_pct=0, opex_pct=0, capital=Capital(discount_rate_pct=10))
    e = calculate_economics(r, {"total_power": 1, "annual_energy": 60}, 1000, 1000)
    assert e["npv_rub"] == pytest.approx(-100 + 60 / 1.1 + 60 / 1.1**2)
    assert e["roi_years"] == pytest.approx(100 / 60)
    assert e["discounted_payback_years"] == pytest.approx(1 + (100 - 60 / 1.1) / (60 / 1.1**2))
    assert e["incremental_product_income"] == 0
    assert e["net_income"] == 60  # Existing agricultural turnover cannot repay new PV.
    assert e["irr_pct"] == pytest.approx(13.066238629, abs=1e-6)


def test_zero_discount_and_negative_cashflows():
    r = request(project_years=3, capex_per_kw=100, degradation_pct=0, opex_pct=0, capital=Capital(discount_rate_pct=0))
    e = calculate_economics(r, {"total_power": 1, "annual_energy": 10}, 0, 0)
    assert e["npv_rub"] == -70
    assert e["roi_years"] is None
    assert e["discounted_payback_years"] is None
    assert crossing_year([-100, -10, -5]) is None
    assert irr([-100, 250, -200]) is None
    assert irr([0, 10]) is None


def test_degradation_water_cost_and_loss_adjustments():
    e = calculate_economics(
        request(project_years=2, degradation_pct=10, capex_per_kw=100, opex_pct=1),
        {"total_power": 1, "annual_energy": 100},
        1000,
        900,
        water_savings=20,
        risk_adjustment=10,
    )
    assert e["cash_flows"][1]["cash_flow_rub"] == 29
    assert e["cash_flows"][2]["cash_flow_rub"] == 19


def test_no_panels_and_consistent_power_rating():
    assert calculate_energy(1, 0, [100] * 12, 0.85)["annual_energy"] == 0
    assert calculate_energy(0.0001, 0.1, [100] * 12, 0.85)["num_panels"] == 0
    e = calculate_energy(10, 0.3, [100] * 12, 0.85)
    assert e["annual_energy"] == pytest.approx(e["total_power"] * 1200 * 0.85)
    assert e["annual_energy"] == pytest.approx(sum(e["monthly_energy"]))
    assert sum(annual_to_monthly(1500, 2024)) == pytest.approx(1500)


def test_water_conservation_and_rainfed():
    w = Water(
        baseline_et_mm=500,
        soil_evaporation_mm=150,
        soil_evaporation_reduction_pct=20,
        effective_rainfall_mm=250,
        irrigation_efficiency=0.8,
        water_cost_rub_m3=10,
    )
    result = water_balance(w, 10)
    assert result["baseline_irrigation_m3_ha"] == 3125
    assert result["av_irrigation_m3_ha"] == 2750
    assert result["saved_m3_year"] == 3750
    assert result["irrigation_reduction_pct"] == 12
    assert result["annual_savings_rub"] == 37500
    w.effective_rainfall_mm = 600
    result = water_balance(w, 10)
    assert result["saved_m3_year"] == 0
    assert result["irrigation_reduction_pct"] is None


def test_water_can_increase_under_panels():
    w = Water(
        baseline_et_mm=500,
        soil_evaporation_mm=150,
        soil_evaporation_reduction_pct=20,
        effective_rainfall_mm=250,
        effective_rainfall_av_mm=150,
        irrigation_efficiency=1,
    )
    assert water_balance(w, 1)["saved_m3_year"] == -700


def test_risk_relative_reduction_and_no_implicit_aggregation():
    risks = Risks(
        drought=Hazard(
            probability_pct=10, loss_fraction_pct=50, probability_reduction_pct=20, severity_reduction_pct=10
        )
    )
    r = crop_risks(risks, 100000, 100000)
    p = r["perils"]["drought"]
    assert p["av_probability_pct"] == pytest.approx(8)
    assert p["probability_reduction_pp"] == pytest.approx(2)
    assert p["baseline_expected_loss_rub"] == 5000
    assert p["av_expected_loss_rub"] == pytest.approx(3600)
    assert r["total_avoided_loss_rub"] is None
    assert r["cash_flow_adjustment_rub"] == 0
    risks.events_are_mutually_exclusive = True
    risks.include_in_cash_flow = True
    assert crop_risks(risks, 100000, 100000)["cash_flow_adjustment_rub"] == pytest.approx(1400)


@pytest.mark.parametrize(
    "updates",
    [
        {"sector": "typo"},
        {"lat": 91},
        {"area_ha": 0},
        {"coverage": -0.1},
        {"energy_price": -1},
        {"height": math.nan},
        {"radiation_monthly": [1] * 12},
        {"coverage": 0, "relative_yield_pct": 90},
        {"unknown_parameter": 10},
    ],
)
def test_invalid_inputs(updates):
    with pytest.raises(ValidationError):
        request(**updates)


def test_invalid_risks_and_water():
    with pytest.raises(ValidationError):
        Risks(include_in_cash_flow=True)
    with pytest.raises(ValidationError):
        Risks(events_are_mutually_exclusive=True, frost=Hazard(probability_pct=80), hail=Hazard(probability_pct=30))
    with pytest.raises(ValidationError):
        Hazard(probability_pct=80, probability_reduction_pct=-50)
    with pytest.raises(ValidationError):
        Water(baseline_et_mm=100, soil_evaporation_mm=101)
