"""Private NSGA-II search, geometry-linked scenarios and public metric projection."""

import json
import uuid
from dataclasses import replace

import numpy as np

from .agronomy import crop_risks, water_balance
from .catalogs import crop_yield
from .coefficients import coefficient, product_catalog
from .economics import calculate_economics
from .energy import annual_to_monthly
from .nsga2 import crowding, search
from .physical_scenario import Environment, evaluate_geometry, illumination, mechanism
from .schemas import CalculationRequest, Water
from .storage import private_directory
from .weather import fetch_weather, season_weather

DEFAULT_POLICY = {
    "pitch_bounds_m": [4.5, 14.0],
    "tilt_bounds_deg": [5.0, 40.0],
    "height_bounds_m": {"crop": [2.5, 5.0], "aqua": [1.0, 3.0], "forest": [5.0, 8.0]},
    "minimum_clearance_m": {"crop": 1.5, "aqua": 0.2, "forest": 4.0},
    "capex_per_kw": {"crop": 60000, "aqua": 70000, "forest": 60000},
    "height_cost_slope": 0.04,
    "opex_pct": 1.5,
    "population_size": 32,
    "generations": 16,
    "seed": 42,
    "display_variants": 12,
}
WEATHER_SCENARIOS = [
    {"id": "base", "label": "Обычная погода", "solar": 1.0, "temp_delta": 0, "rain": 1.0, "diffuse": 0.35},
    {"id": "hot_dry", "label": "Жарко и сухо", "solar": 1.1, "temp_delta": 4, "rain": 0.6, "diffuse": 0.25},
    {"id": "cloudy_wet", "label": "Прохладно и облачно", "solar": 0.85, "temp_delta": -2, "rain": 1.25, "diffuse": 0.5},
]
ECONOMIC_SCENARIOS = [
    {"id": "base", "label": "Базовая экономика", "tariff": 1, "price": 1, "capex": 1, "rate_delta": 0},
    {
        "id": "downside",
        "label": "Рост затрат и снижение цен",
        "tariff": 0.8,
        "price": 0.85,
        "capex": 1.2,
        "rate_delta": 3,
    },
    {"id": "upside", "label": "Благоприятная экономика", "tariff": 1.1, "price": 1.1, "capex": 0.95, "rate_delta": -2},
]


def owner_policy():
    path = private_directory() / "policy.json"
    policy = {**DEFAULT_POLICY, **(json.loads(path.read_text()) if path.exists() else {})}

    def valid_bounds(values, minimum, maximum):
        return (
            isinstance(values, list)
            and len(values) == 2
            and all(isinstance(v, (int, float)) and np.isfinite(v) for v in values)
            and minimum <= values[0] < values[1] <= maximum
        )

    if not valid_bounds(policy["pitch_bounds_m"], 2.134, 100) or not valid_bounds(policy["tilt_bounds_deg"], 0, 85):
        raise ValueError("Некорректные границы конфигурации в закрытых настройках")
    for sector in ("crop", "aqua", "forest"):
        if not valid_bounds(policy["height_bounds_m"].get(sector), 0.1, 50):
            raise ValueError("Некорректные границы высоты")
        for key in ("minimum_clearance_m", "capex_per_kw"):
            value = policy[key].get(sector)
            if not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
                raise ValueError("Некорректные закрытые настройки стоимости или просвета")
    for key, upper in (("height_cost_slope", 1), ("opex_pct", 100)):
        if not np.isfinite(policy[key]) or not 0 <= policy[key] <= upper:
            raise ValueError("Некорректные закрытые настройки затрат")
    for key, lower, upper in (
        ("population_size", 8, 100),
        ("generations", 1, 100),
        ("display_variants", 3, 30),
        ("seed", 0, 2**32 - 1),
    ):
        if type(policy[key]) is not int or not lower <= policy[key] <= upper:
            raise ValueError("Некорректные настройки NSGA-II")
    if policy["population_size"] * (policy["generations"] + 1) > 2000:
        raise ValueError("Слишком большой бюджет вычислений NSGA-II")
    return policy


def resolve_baseline(request):
    if request.base_productivity is not None:
        return request, {"source": "user", "label": "Введено пользователем"}
    if request.sector != "crop":
        raise ValueError("Укажите базовую продуктивность на гектар.")
    try:
        value, source = crop_yield(request.product_name, request.country)
    except ValueError as exc:
        raise ValueError("Для этой культуры и страны нет базовой продуктивности. Выберите ручной ввод.") from exc
    if value <= 0:
        raise ValueError("Не найдена положительная базовая урожайность")
    return request.model_copy(update={"base_productivity": value}), {
        **source,
        "label": f"FAO: {request.country}, {source['year']}, средняя урожайность страны",
    }


def optimize(request):
    if request.product_name not in {item["name"] for item in product_catalog()[request.sector]}:
        raise ValueError("Выберите культуру или вид из списка")
    request, baseline_source = resolve_baseline(request)
    if request.sector != "crop" and (request.water or request.risks):
        raise ValueError("Полив и риски урожая доступны для растениеводства")
    estimate = coefficient(request.sector, request.product_name, request.base_productivity)
    if request.radiation_annual is None:
        weather = fetch_weather(request.lat, request.lon, request.weather_year)
        monthly = weather["monthly"]
        seasonal = season_weather(weather, request.season_start_month, request.season_end_month)
        request = request.model_copy(update={k: v for k, v in seasonal.items() if k != "radiation_annual"})
    else:
        monthly = annual_to_monthly(request.radiation_annual, request.weather_year)
        weather = {"source": "user_annual", "annual": request.radiation_annual, "monthly": monthly}
    if sum(monthly) <= 0:
        raise ValueError("Для подбора вариантов требуется положительная солнечная радиация")
    policy = owner_policy()
    bounds = [policy["pitch_bounds_m"], policy["height_bounds_m"][request.sector], policy["tilt_bounds_deg"]]
    env = Environment(
        request.lat,
        tuple(monthly),
        request.growing_season_temperature_c,
        request.growing_season_rainfall_mm,
        request.irrigation_mm,
        request.water.baseline_et_mm if request.water else 500,
        request.weather_year,
        request.water.soil_evaporation_mm / max(request.water.baseline_et_mm, 1) if request.water else 0.3,
    )
    reference_geometry = [8.0, float(np.mean(bounds[1])), 25.0]
    reference_light = illumination(*reference_geometry, env)
    reference = mechanism(reference_light, reference_geometry[1], env, request.sector)
    multiplier = 1000 if request.sector == "aqua" else 1
    baseline_income = request.base_productivity * request.product_price * request.area_ha * multiplier
    base_cost = policy["capex_per_kw"][request.sector]

    def evaluate(geometry, w=WEATHER_SCENARIOS[0], economic=ECONOMIC_SCENARIOS[0], calibration=1):
        local_env = replace(
            env,
            monthly_ghi=tuple(v * w["solar"] for v in env.monthly_ghi),
            air_temperature_c=env.air_temperature_c + w["temp_delta"],
            rainfall_mm=env.rainfall_mm * w["rain"],
            et_demand_mm=env.et_demand_mm * max(0.5, 1 + 0.025 * w["temp_delta"]),
        )
        candidate = evaluate_geometry(
            geometry,
            local_env,
            request.sector,
            estimate["factor"],
            reference,
            request.area_ha,
            calibration,
            w["diffuse"],
        )
        config = candidate["configuration"]
        capex_kw = base_cost * (1 + policy["height_cost_slope"] * max(0, config["height_m"] - 3)) * economic["capex"]
        internal = CalculationRequest(
            sector=request.sector,
            lat=request.lat,
            lon=request.lon,
            area_ha=request.area_ha,
            coverage=config["gcr"],
            height=config["height_m"],
            energy_price=request.energy_price * economic["tariff"],
            capital={
                "method": "direct",
                "discount_rate_pct": float(np.clip(request.capital.rate() * 100 + economic["rate_delta"], 0, 100)),
            },
            project_years=request.project_years,
            capex_per_kw=capex_kw,
            opex_pct=policy["opex_pct"],
        )
        water = {"status": "not_configured"}
        if request.water:
            demand_scale = max(0.5, 1 + 0.025 * w["temp_delta"])
            water_input = Water(
                **{
                    **request.water.model_dump(),
                    "baseline_et_mm": request.water.baseline_et_mm * demand_scale,
                    "soil_evaporation_mm": request.water.soil_evaporation_mm * demand_scale,
                    "soil_evaporation_reduction_pct": 80 * candidate["microclimate"]["shade_fraction"],
                    "effective_rainfall_mm": request.water.effective_rainfall_mm * w["rain"],
                    "effective_rainfall_av_mm": (
                        request.water.effective_rainfall_av_mm
                        if request.water.effective_rainfall_av_mm is not None
                        else request.water.effective_rainfall_mm
                    )
                    * w["rain"],
                }
            )
            water = water_balance(water_input, request.area_ha)
        product_income = baseline_income * economic["price"] * candidate["productivity_factor"]
        risks = {"status": "not_configured"}
        if request.risks:
            # User reductions are upper-bound scenario efficacies, scaled by exposure.
            risk_inputs = request.risks.model_copy(deep=True)
            for hazard_name in ("frost", "drought", "hail"):
                hazard = getattr(risk_inputs, hazard_name)
                exposure = (
                    config["projected_coverage"]
                    if hazard_name == "hail"
                    else candidate["microclimate"]["shade_fraction"]
                )
                hazard.probability_reduction_pct *= exposure
                hazard.severity_reduction_pct *= exposure
            risks = crop_risks(risk_inputs, baseline_income * economic["price"], product_income)
        economics = calculate_economics(
            internal,
            candidate["energy"],
            baseline_income * economic["price"],
            product_income,
            water.get("annual_savings_rub", 0),
            risks.get("cash_flow_adjustment_rub", 0),
        )
        candidate.update(economics=economics, water=water, risks=risks)
        return candidate

    evaluated = {}

    def objectives(geometry):
        candidate = evaluate(geometry)
        config = candidate["configuration"]
        bottom = config["height_m"] - 2.134 * np.sin(np.radians(config["tilt_deg"])) / 2
        violation = max(0, policy["minimum_clearance_m"][request.sector] - bottom)
        if request.budget_rub is not None:
            violation += max(0, candidate["economics"]["capex"] / request.budget_rub - 1)
        if candidate["energy"]["num_panels"] < 1:
            violation += 1
        candidate["feasible"] = violation == 0
        evaluated[tuple(geometry)] = candidate
        if violation:
            return np.full(3, 2 + violation)
        # Fixed monotonic scaling preserves dominance and makes constraint penalties safe.
        return np.arctan(
            [
                -candidate["productivity_factor"],
                -candidate["energy"]["annual_energy"] / request.area_ha / 1e6,
                -candidate["economics"]["npv_rub"] / request.area_ha / 1e6,
            ]
        )

    geometry_set, objective_values, count = search(
        objectives, bounds, policy["population_size"], policy["generations"], policy["seed"]
    )
    feasible = [i for i, g in enumerate(geometry_set) if evaluated[tuple(g)]["feasible"]]
    if not feasible:
        raise ValueError("При заданной площади и бюджете допустимые варианты не найдены")
    geometry_set, objective_values = geometry_set[feasible], objective_values[feasible]
    full_front_count = len(geometry_set)
    # A diverse representative subset; retain extrema in each objective.
    limit = min(int(policy["display_variants"]), full_front_count)
    indices = np.argsort(-crowding(objective_values), kind="stable")[:limit]
    selected_geometry = sorted(geometry_set[indices], key=lambda g: evaluated[tuple(g)]["energy"]["annual_energy"])
    variants = []
    for geometry in selected_geometry:
        candidate = evaluated[tuple(geometry)]
        scenarios = []
        for w in WEATHER_SCENARIOS:
            for economic in ECONOMIC_SCENARIOS:
                result = evaluate(geometry, w, economic)
                scenarios.append(
                    {
                        "name": f"{w['label']} / {economic['label']}",
                        "productivity": request.base_productivity * result["productivity_factor"],
                        "generation_kwh": result["energy"]["annual_energy"],
                        "npv_rub": result["economics"]["npv_rub"],
                    }
                )
        sensitivity = [evaluate(geometry, calibration=m)["productivity_factor"] for m in (0.75, 1.25)]
        candidate.update(
            id=uuid.uuid4().hex,
            scenarios=scenarios,
            robustness={
                "scenario_count": len(scenarios),
                "positive_npv_scenarios": sum(s["npv_rub"] >= 0 for s in scenarios),
                "npv_min": min(s["npv_rub"] for s in scenarios),
                "npv_max": max(s["npv_rub"] for s in scenarios),
                "productivity_min": min(s["productivity"] for s in scenarios),
                "productivity_max": max(s["productivity"] for s in scenarios),
                "generation_min": min(s["generation_kwh"] for s in scenarios),
                "generation_max": max(s["generation_kwh"] for s in scenarios),
            },
            calibration_sensitivity=sensitivity,
        )
        variants.append(candidate)
    return {
        "variants": variants,
        "selected_variant_id": None,
        "resolved_inputs": request.model_dump(),
        "baseline_source": baseline_source,
        "coefficient": estimate,
        "weather": weather,
        "policy": policy,
        "reference_geometry": reference_geometry,
        "reference_mechanism": reference,
        "search": {
            "algorithm": "NSGA-II",
            "evaluations": count,
            "nondominated_found": full_front_count,
            "displayed": len(variants),
            "seed": policy["seed"],
            "exact_optimum": False,
        },
        "scenario_definitions": {"weather": WEATHER_SCENARIOS, "economics": ECONOMIC_SCENARIOS},
        "limitation": "Геометрическая модель освещения и сценарная модель биологического отклика, не полевая валидация",
    }


def public_variant(request, candidate):
    economics = candidate["economics"]
    units = {"crop": "кг/га/год", "aqua": "т/га/год", "forest": "м³/га/год"}
    return {
        "id": candidate["id"],
        "energy": {
            "installed_power_kw": candidate["energy"]["total_power"],
            "annual_generation_kwh": candidate["energy"]["annual_energy"],
        },
        "productivity": {
            "baseline": request.base_productivity,
            "estimated": request.base_productivity * candidate["productivity_factor"],
            "unit": units[request.sector],
            "change_pct": (candidate["productivity_factor"] - 1) * 100,
        },
        "economics": {
            k: economics[k]
            for k in (
                "capex",
                "net_income",
                "npv_rub",
                "irr_pct",
                "roi_years",
                "discounted_payback_years",
                "discount_rate_pct",
                "project_years",
            )
        },
        "water": candidate["water"],
        "risks": candidate["risks"],
        "robustness": candidate["robustness"],
        "scenarios": candidate["scenarios"],
    }


def public_result(request, record, calculation_id):
    request = request.model_copy(update={"base_productivity": record["resolved_inputs"]["base_productivity"]})
    return {
        "calculation_id": calculation_id,
        "product_name": request.product_name,
        "sector": request.sector,
        "area_ha": request.area_ha,
        "baseline_source": "Данные хозяйства"
        if record["baseline_source"].get("source") == "user"
        else "Средняя продуктивность страны",
        "variants": [public_variant(request, variant) for variant in record["variants"]],
        "selected_variant_id": record["selected_variant_id"],
        "weather": {
            "radiation_annual": record["weather"]["annual"],
            **{
                key: record["resolved_inputs"][key]
                for key in ("growing_season_temperature_c", "growing_season_rainfall_mm")
            },
        },
        "search": {"algorithm": "NSGA-II", "displayed": len(record["variants"]), "approximate": True},
    }
