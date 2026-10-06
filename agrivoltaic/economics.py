"""Incremental unlevered project cash flows against farming without PV."""

from .schemas import CalculationRequest


def crossing_year(cash_flows: list[float]) -> float | None:
    if cash_flows[0] == 0:
        return 0.0 if any(cf > 0 for cf in cash_flows[1:]) else None
    balance = cash_flows[0]
    for year, flow in enumerate(cash_flows[1:], 1):
        if balance < 0 <= balance + flow:
            return year - 1 + (-balance / flow)
        balance += flow
    return None


def irr(cash_flows: list[float]) -> float | None:
    nonzero = [v for v in cash_flows if v]
    if not nonzero or nonzero[0] >= 0 or sum(a * b < 0 for a, b in zip(nonzero, nonzero[1:])) != 1:
        return None

    def npv(rate):
        return sum(value / (1 + rate) ** year for year, value in enumerate(cash_flows))

    low, high = -0.99, 1.0
    while npv(high) > 0 and high < 1e6:
        high *= 2
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(150):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2 * 100


def calculate_economics(
    request: CalculationRequest,
    energy: dict,
    baseline_income: float,
    product_income: float,
    water_savings: float = 0,
    risk_adjustment: float = 0,
) -> dict:
    cost = request.capex_per_kw
    if cost is None:
        cost = 70000 if request.sector == "aqua" else 60000
    capex = energy["total_power"] * cost + request.fixed_capex_rub
    opex = capex * request.opex_pct / 100 + request.annual_extra_cost_rub
    rate = request.capital.rate()
    flows, discounted = [-capex], [-capex]
    schedule = [
        {"year": 0, "cash_flow_rub": -capex, "discounted_cash_flow_rub": -capex, "cumulative_discounted_rub": -capex}
    ]
    delta_crop = product_income - baseline_income
    for year in range(1, request.project_years + 1):
        revenue = energy["annual_energy"] * (1 - request.degradation_pct / 100) ** (year - 1) * request.energy_price
        flow = revenue + delta_crop + water_savings + risk_adjustment - opex
        flow -= max(flow, 0) * request.annual_cash_flow_tax_pct / 100
        pv = flow / (1 + rate) ** year
        flows.append(flow)
        discounted.append(pv)
        schedule.append(
            {
                "year": year,
                "energy_income_rub": revenue,
                "cash_flow_rub": flow,
                "discounted_cash_flow_rub": pv,
                "cumulative_discounted_rub": sum(discounted),
            }
        )
    return {
        "capex": capex,
        "opex": opex,
        "energy_income": energy["annual_energy"] * request.energy_price,
        "baseline_product_income": baseline_income,
        "product_income": product_income,
        "incremental_product_income": delta_crop,
        "total_income": energy["annual_energy"] * request.energy_price + product_income,
        "water_savings": water_savings,
        "risk_adjustment": risk_adjustment,
        "net_income": flows[1],
        "roi_years": crossing_year(flows),
        "discounted_payback_years": crossing_year(discounted),
        "npv_rub": sum(discounted),
        "irr_pct": irr(flows),
        "discount_rate_pct": rate * 100,
        "capital_method": request.capital.method,
        "project_years": request.project_years,
        "cash_flows": schedule,
        "cash_flow_basis": "incremental_unlevered",
        "currency": "RUB",
        "price_basis": "constant prices; use a consistent real discount rate",
    }
