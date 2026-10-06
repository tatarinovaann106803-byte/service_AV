import numpy as np
import pytest

from agrivoltaic.coefficients import coefficient
from agrivoltaic.nsga2 import fronts, search
from agrivoltaic.optimization import optimize
from agrivoltaic.physical_scenario import Environment, evaluate_geometry, illumination, mechanism
from agrivoltaic.public_schemas import PublicRequest


def test_nsga2_known_front_and_determinism():
    def objective(x):
        return [x[0] ** 2 + x[1] ** 2, (x[0] - 1) ** 2 + x[1] ** 2]

    a, f, count = search(objective, [[0, 1], [0, 1]], 32, 20)
    b, g, other_count = search(objective, [[0, 1], [0, 1]], 32, 20)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(f, g)
    assert count == other_count and count > 32
    assert len(fronts(f)) == 1
    assert np.all((a >= 0) & (a <= 1))
    assert np.median(a[:, 1]) < 0.05
    assert a[:, 0].min() < 0.05 and a[:, 0].max() > 0.95
    with pytest.raises(ValueError):
        search(lambda x: [float("nan")], [[0, 1]])


def test_individual_row_means():
    wheat = coefficient("crop", "Пшеница", 4000)
    assert wheat["rows"] == 4 and wheat["factor"] == pytest.approx(0.755)
    assert coefficient("forest", "Тополь", 5)["factor"] == pytest.approx(0.61)
    shrimp = coefficient("aqua", "Белоногая креветка", 5)
    assert shrimp["factor"] == pytest.approx(1.4)
    assert coefficient("aqua", "fish", 5)["rows"] == 0


def test_geometry_changes_light_climate_productivity_and_energy():
    env = Environment(45, (1400 / 12,) * 12)
    ref = mechanism(illumination(8, 3, 25, env), 3, env, "crop")
    base = evaluate_geometry([8, 3, 25], env, "crop", 0.755, ref, 10)
    assert base["productivity_factor"] == pytest.approx(0.755)
    for geometry in ([5, 3, 25], [8, 5, 25], [8, 3, 40]):
        variant = evaluate_geometry(geometry, env, "crop", 0.755, ref, 10)
        assert variant["light"] != base["light"]
        assert variant["microclimate"] != base["microclimate"]
        assert variant["productivity_factor"] != base["productivity_factor"]
        assert 0 <= variant["light"]["par_ratio"] <= 1
    assert evaluate_geometry([5, 3, 25], env, "crop", 0.755, ref, 10)["energy"] != base["energy"]


def test_full_search_is_nondominated_and_scenario_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRIVOLTAIC_PRIVATE_DIR", str(tmp_path))
    request = PublicRequest(
        lat=45, lon=38, area_ha=10, product_name="Пшеница", product_price=15, energy_price=6, radiation_annual=1400
    )
    record = optimize(request)
    objectives = [
        [-v["productivity_factor"], -v["energy"]["annual_energy"], -v["economics"]["npv_rub"]]
        for v in record["variants"]
    ]
    assert len(fronts(objectives)) == 1
    assert len(objectives) > 2
    assert record["selected_variant_id"] is None
    assert record["search"]["exact_optimum"] is False
    for v in record["variants"]:
        scenarios = v["scenarios"]
        assert len(scenarios) == 9
        assert len({s["generation_kwh"] for s in scenarios}) == 3
        assert v["robustness"]["npv_min"] < v["robustness"]["npv_max"]
