"""Artificial fixtures below test software, not agronomic validity."""

import joblib
import numpy as np
import pandas as pd
import pytest

from agrivoltaic.model_contract import FEATURES, MICROCLIMATE, yield_spec
from agrivoltaic.prediction import model_status, predict_yield
from agrivoltaic.schemas import CalculationRequest
from agrivoltaic.training import train, validate_observations


def fixture_frame():
    rng = np.random.default_rng(8)
    n = 120
    frame = pd.DataFrame(
        {
            "coverage": rng.uniform(0.1, 0.7, n),
            "height": rng.uniform(2, 5, n),
            "air_temperature_open_c": rng.uniform(15, 30, n),
            "relative_humidity_open_pct": rng.uniform(30, 80, n),
            "ghi_kwh_m2_day": rng.uniform(3, 7, n),
            "wind_speed_m_s": rng.uniform(1, 5, n),
            "soil_moisture_open_pct": rng.uniform(20, 40, n),
            "study_id": [f"test-study-{i // 10}" for i in range(n)],
            "source_doi": [f"10.9999/test-fixture-{i // 10}" for i in range(n)],
            "reviewed": True,
            "system_type": "fixed",
        }
    )
    frame["air_temperature_delta_c"] = -4 * frame.coverage + 0.1 * frame.height
    frame["soil_temperature_delta_c"] = -6 * frame.coverage + 0.1 * frame.height
    frame["et_reduction_pct"] = 20 * frame.coverage + frame.height
    return frame


def test_missing_and_unreviewed_data_never_promoted(tmp_path):
    report = train(tmp_path / "missing.csv", tmp_path)
    assert report["status"] == "insufficient_data"
    assert not list(tmp_path.glob("*.joblib"))
    f = fixture_frame()
    f.loc[0, "reviewed"] = False
    with pytest.raises(ValueError, match="reviewed"):
        validate_observations(f)
    f = fixture_frame()
    with pytest.raises(ValueError, match="не менее"):
        validate_observations(f.iloc[:20])
    f = fixture_frame()
    f.loc[0, "height"] = np.nan
    with pytest.raises(ValueError, match="Пропуски"):
        validate_observations(f)


def test_grouped_training_and_inference_use_same_contract(tmp_path, monkeypatch):
    f = fixture_frame()
    path = tmp_path / "test_only.csv"
    f.to_csv(path, index=False)
    report = train(path, tmp_path)
    assert report["status"] == "validated", report
    assert not set(report["train_studies"]) & set(report["test_studies"])
    bundle = joblib.load(tmp_path / report["artifact"])
    # The persisted scaler is fitted on the training partition, not on all data.
    train_rows = f[f.source_doi.isin(report["train_studies"])]
    actual_means = bundle.regressor_.steps[0][1].mean_
    assert actual_means == pytest.approx(train_rows[FEATURES].mean().to_numpy())
    assert not np.allclose(actual_means, f[FEATURES].mean().to_numpy())
    monkeypatch.setattr("agrivoltaic.prediction.MODELS", tmp_path)
    assert model_status()["status"] == "validated"
    from agrivoltaic.prediction import predict

    values = {key: float(f[key].mean()) for key in FEATURES}
    assert predict(MICROCLIMATE, values, "fixed")["status"] == "model_prediction"
    values["coverage"] = 1
    assert predict(MICROCLIMATE, values, "fixed")["status"] == "out_of_domain"
    assert predict(MICROCLIMATE, None, "fixed")["status"] == "missing_inputs"
    assert predict(MICROCLIMATE, values, "tracking")["status"] == "unsupported_configuration"
    (tmp_path / report["artifact"]).write_bytes(b"corrupt")
    assert model_status()["status"] == "unavailable"


def test_yield_model_per_crop_and_absolute_prediction(tmp_path, monkeypatch):
    f = fixture_frame()
    f["crop_name"] = "Test crop"
    f["growing_season_temperature_c"] = f.air_temperature_open_c
    f["growing_season_rainfall_mm"] = f.ghi_kwh_m2_day * 50
    f["irrigation_mm"] = f.soil_moisture_open_pct * 10
    f["relative_yield_pct"] = 100 - 20 * f.coverage + f.height
    spec = yield_spec("Test crop")
    path = tmp_path / "test_only.csv"
    f.to_csv(path, index=False)
    report = train(path, tmp_path, spec)
    assert report["status"] == "validated", report
    monkeypatch.setattr("agrivoltaic.prediction.MODELS", tmp_path)
    r = CalculationRequest(
        lat=45,
        lon=38,
        area_ha=1,
        coverage=0.4,
        height=3.5,
        energy_price=6,
        crop_name="Test crop",
        productivity_mode="model",
        yield_inputs={"growing_season_temperature_c": 22, "growing_season_rainfall_mm": 250, "irrigation_mm": 300},
    )
    result = predict_yield(r)
    assert result["status"] == "model_prediction", result
    assert result["values"]["relative_yield_pct"] == pytest.approx(95.5, abs=5)
    r.crop_name = "Different crop"
    assert predict_yield(r)["status"] == "unavailable"
    with pytest.raises(ValueError, match="культуру"):
        validate_observations(f, yield_spec("Different crop"))


def test_failed_quality_gate_rejects_bad_model(tmp_path, monkeypatch):
    from sklearn.dummy import DummyRegressor

    f = fixture_frame()
    path = tmp_path / "test_only.csv"
    f.to_csv(path, index=False)
    monkeypatch.setattr("agrivoltaic.training.estimator", lambda kind: DummyRegressor())
    result = train(path, tmp_path)
    assert result["status"] == "rejected"
    assert not list(tmp_path.glob("*.joblib"))


def test_same_experiment_across_publications_stays_in_one_group():
    f = fixture_frame()
    f.loc[10:19, "study_id"] = "test-study-0"
    _, _, groups = validate_observations(f)
    assert groups.iloc[0] == groups.iloc[15]
    assert groups.nunique() == 11
