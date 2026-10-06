import csv
from types import SimpleNamespace

import pytest
import requests

from agrivoltaic.catalogs import crop_price, crop_yield, read_catalog
from agrivoltaic.ingestion import current_raw, refresh_sources
from agrivoltaic.preparation import number
from agrivoltaic.weather import WeatherUnavailable, parse_monthly


def nasa_payload(unit="kW-hr/m^2/day"):
    values = {f"2024{m:02}": 5 for m in range(1, 13)}
    return {
        "header": {"fill_value": -999},
        "parameters": {"ALLSKY_SFC_SW_DWN": {"units": unit}, "T2M": {"units": "C"}, "PRECTOTCORR": {"units": "mm/day"}},
        "properties": {
            "parameter": {
                "ALLSKY_SFC_SW_DWN": {**values, "202413": 100000},
                "T2M": {**values, "202413": 100000},
                "PRECTOTCORR": {**values, "202413": 100000},
            }
        },
    }


def test_nasa_units_leap_year_and_ignore_annual_summary():
    result = parse_monthly(nasa_payload(), 2024)
    assert result["annual"] == 5 * 366
    assert result["monthly"][1] == 5 * 29
    assert result["temperature"] == 5
    result = parse_monthly(nasa_payload("MJ/m^2/day"), 2024)
    assert result["annual"] == pytest.approx(5 * 366 / 3.6)


@pytest.mark.parametrize("bad", [None, -999, float("nan"), -1])
def test_missing_or_invalid_weather_is_not_zero(bad):
    data = nasa_payload()
    data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["202403"] = bad
    with pytest.raises(WeatherUnavailable):
        parse_monthly(data, 2024)


def test_unknown_nasa_units():
    with pytest.raises(WeatherUnavailable):
        parse_monthly(nasa_payload("W/m2"), 2024)


def test_actual_yield_and_catalog_path_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    value, meta = crop_yield("Пшеница", "Russian Federation")
    assert value > 0
    assert meta["year"] == 2024
    assert meta["original_unit"] == "kg/ha"
    assert crop_yield("Кукуруза", "Russian Federation")[0] > 0
    assert crop_yield("Соя", "Russian Federation")[0] > 0
    with pytest.raises(ValueError):
        crop_price("Неизвестная культура", "Нет региона")
    with pytest.raises(ValueError):
        crop_yield("Нет культуры", "Russian Federation")


def test_yield_unit_conversion_and_latest_year(monkeypatch):
    import agrivoltaic.catalogs as catalogs

    monkeypatch.setattr(
        catalogs,
        "read_catalog",
        lambda _: [
            {"Area": "X", "Item": "Wheat", "Element": "Yield", "Year": "2020", "Unit": "kg/ha", "Value": "100"},
            {"Area": "X", "Item": "Wheat", "Element": "Yield", "Year": "2024", "Unit": "hg/ha", "Value": "30000"},
        ],
    )
    assert catalogs.crop_yield("Пшеница", "X")[0] == 3000


def test_reference_schema_rejected(tmp_path):
    (tmp_path / "crop_prices.csv").write_text("wrong,header\n1,2\n")
    with pytest.raises(ValueError):
        read_catalog("crop_prices.csv", tmp_path)


def response(content):
    return SimpleNamespace(
        content=content,
        headers={"Content-Type": "text/csv"},
        url="https://example.org/data.csv",
        raise_for_status=lambda: None,
    )


def test_failed_refresh_preserves_previous_snapshot(tmp_path):
    original = b'{"snapshot":"previous"}'
    (tmp_path / "current.json").write_bytes(original)
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return response("DOI,Культура\n10.1234/a,Пшеница\n".encode())
        raise requests.Timeout("Test failure")

    with pytest.raises(requests.Timeout):
        refresh_sources(tmp_path, fetch)
    assert (tmp_path / "current.json").read_bytes() == original
    assert not (tmp_path / "snapshots").exists()


def test_snapshot_lineage_and_checksum(tmp_path):
    bodies = iter(
        [
            "DOI,Культура\n10.1234/a,Пшеница\n",
            "DOI,Woody_Species\n10.1234/b,Pine\n",
            "DOI,Aquatic organism\n10.1234/c,Carp\n",
        ]
    )
    pointer = refresh_sources(tmp_path, lambda *a, **kw: response(next(bodies).encode()))
    assert pointer["rows"] == 3
    raw = current_raw(tmp_path)
    rows = list(csv.DictReader(raw.open()))
    assert [r["source_id"] for r in rows] == ["crop_farming", "forestry", "aquaculture"]
    assert all(r["source_row"] == "2" for r in rows)
    raw.write_text("corrupt")
    with pytest.raises(ValueError):
        current_raw(tmp_path)


def test_ambiguous_measurements_not_guessed():
    assert number("29,5%", percent=True) == 0.295
    assert number("-2,83") == -2.83
    assert number("10–20%") is None
    assert number("нет данных") is None


def test_nasa_precipitation_and_season():
    from agrivoltaic.weather import season_weather

    weather = parse_monthly(nasa_payload(), 2024)
    assert weather["precipitation_annual_mm"] == 5 * 366
    season = season_weather(weather, 4, 9)
    assert season["growing_season_rainfall_mm"] == 5 * 183
    assert season["growing_season_temperature_c"] == 5
    weather["temperature_monthly"][1] = 15
    winter = season_weather(weather, 12, 2)
    assert winter["growing_season_temperature_c"] == pytest.approx((5 * 62 + 15 * 29) / 91)
    bad = nasa_payload()
    bad["properties"]["parameter"]["PRECTOTCORR"]["202405"] = -999
    with pytest.raises(WeatherUnavailable):
        parse_monthly(bad, 2024)
