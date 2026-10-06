import json

import pytest
from fastapi.testclient import TestClient

from agrivoltaic.api import app
from agrivoltaic.owner import create_owner_app
from agrivoltaic.storage import get_calculation
from agrivoltaic.weather import WeatherUnavailable

client = TestClient(app)
BASE = dict(lat=45, lon=38, area_ha=10, product_name="Пшеница", product_price=15, energy_price=6, radiation_annual=1400)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRIVOLTAIC_PRIVATE_DIR", str(tmp_path))
    (tmp_path / "policy.json").write_text(json.dumps({"population_size": 12, "generations": 3}))


def calculate(**changes):
    response = client.post("/calculate", json={**BASE, **changes})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_public_contract_and_explicit_choice():
    assert client.get("/calculator").status_code == 200
    data = calculate()
    assert data["selected_variant_id"] is None
    assert len(data["variants"]) > 1
    assert data["variants"][0]["productivity"]["baseline"] == 3064.1
    for v in data["variants"]:
        assert set(v["energy"]) == {"installed_power_kw", "annual_generation_kwh"}
        assert len(v["scenarios"]) == 9
    cid, vid = data["calculation_id"], data["variants"][-1]["id"]
    assert client.post(f"/calculations/{cid}/selection", json={"variant_id": vid}).status_code == 200
    public = client.get(f"/calculations/{cid}").json()["data"]
    assert public["selected_variant_id"] == vid
    private = get_calculation(cid)
    assert private["selected_variant_id"] == vid
    assert "configuration" in private["variants"][0]
    forbidden = {
        "configuration",
        "pitch_m",
        "height_m",
        "tilt_deg",
        "gcr",
        "num_panels",
        "policy",
        "reference_geometry",
        "lineage",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value))
        return set()

    assert not keys(public) & forbidden
    assert client.post(f"/calculations/{cid}/selection", json={"variant_id": "0" * 32}).status_code == 422
    assert client.get("/calculations/missing").status_code == 404


def test_owner_is_separate_and_authenticated():
    data = calculate()
    owner = TestClient(create_owner_app("a-test-password-123"))
    path = f"/api/calculations/{data['calculation_id']}"
    assert owner.get(path).status_code == 401
    assert owner.get(path, auth=("owner", "wrong")).status_code == 401
    assert "configuration" in owner.get(path, auth=("owner", "a-test-password-123")).json()["variants"][0]
    assert owner.get("/", auth=("owner", "a-test-password-123")).status_code == 200
    for path in (path, "/owner", "/private/calculations.sqlite3", "/models/status"):
        assert client.get(path).status_code == 404
    schema = client.get("/openapi.json").text
    assert '"coverage"' not in schema and '"height"' not in schema


@pytest.mark.parametrize(
    "sector,product", [("aqua", "Pacific white shrimp Litopenaeus vannamei"), ("forest", "Poplar")]
)
def test_non_crop_baseline_is_manual(sector, product):
    assert client.post("/calculate", json={**BASE, "sector": sector, "product_name": product}).status_code == 422
    assert calculate(sector=sector, product_name=product, base_productivity=5)["variants"]


def test_water_capital_and_zero_price():
    data = calculate(
        product_price=0,
        capital={
            "method": "wacc",
            "equity_share_pct": 50,
            "cost_of_equity_pct": 14,
            "cost_of_debt_pct": 10,
            "tax_rate_pct": 20,
        },
        water={"baseline_et_mm": 500, "soil_evaporation_mm": 150, "effective_rainfall_mm": 250},
    )
    for v in data["variants"]:
        assert v["economics"]["discount_rate_pct"] == 11
        assert v["water"]["saved_m3_year"] > 0


def test_failure_and_invalid_inputs(monkeypatch):
    def fail(*args):
        raise WeatherUnavailable("Источник недоступен")

    monkeypatch.setattr("agrivoltaic.optimization.fetch_weather", fail)
    assert client.post("/calculate", json={**BASE, "radiation_annual": None}).status_code == 503
    for extra in (
        {"lat": "NaN"},
        {"coverage": 0.3},
        {"sector": "unknown"},
        {"budget_rub": 1},
        {"water": {"baseline_et_mm": 10, "soil_evaporation_mm": 20}},
    ):
        assert client.post("/calculate", json={**BASE, **extra}).status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_raw_nonfinite_json(value):
    body = json.dumps(BASE).replace('"lat": 45', '"lat": ' + value)
    assert client.post("/calculate", content=body, headers={"Content-Type": "application/json"}).status_code == 422


def test_nasa_loaded_values_used_in_calculation(monkeypatch):
    from test_sources import nasa_payload

    from agrivoltaic.weather import parse_monthly

    weather = parse_monthly(nasa_payload(), 2024)
    monkeypatch.setattr("agrivoltaic.optimization.fetch_weather", lambda *args: weather)
    monkeypatch.setattr("agrivoltaic.api.fetch_weather", lambda *args: weather)
    data = calculate(radiation_annual=None, season_start_month=4, season_end_month=9)
    assert data["weather"]["growing_season_temperature_c"] == 5
    assert data["weather"]["growing_season_rainfall_mm"] == 915
    preview = client.get("/weather?lat=45&lon=38&year=2024&start_month=4&end_month=9")
    assert preview.json()["growing_season_rainfall_mm"] == 915
    assert "FAO" not in json.dumps(data, ensure_ascii=False)
    assert "notes" not in data


def test_catalog_and_embed_origins(monkeypatch):
    from agrivoltaic.coefficients import coefficient

    catalog = client.get("/products").json()
    assert len(catalog["crop"]) == 32
    assert all(coefficient("crop", item["name"], 4000)["rows"] > 0 for item in catalog["crop"])
    assert client.post("/calculate", json={**BASE, "product_name": "Алоэ вера"}).status_code == 422
    monkeypatch.setenv("TILDA_ORIGINS", "https://farm.example, *")
    assert client.get("/integration/config").json() == {"parent_origins": ["https://farm.example"]}
