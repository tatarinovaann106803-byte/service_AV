"""Reviewed model bundles, shared feature contracts and no fabricated predictions."""

import hashlib
import json
import logging
import math
from functools import lru_cache

from .config import MODELS
from .model_contract import CONTRACT_VERSION, MICROCLIMATE, yield_spec

logger = logging.getLogger(__name__)


def model_status(spec=MICROCLIMATE) -> dict:
    path = MODELS / f"{spec.name}_manifest.json"
    if not path.exists():
        return {
            "status": "unavailable",
            "reason": "Нет проверенной модели для этих условий",
            "legacy_models": "disabled_synthetic_features",
        }
    try:
        manifest = json.loads(path.read_text())
        if manifest.get("status") in ("insufficient_data", "rejected"):
            return {
                "status": manifest["status"],
                "reason": "Недостаточно проверенных полевых наблюдений"
                if manifest["status"] == "insufficient_data"
                else "Модель не прошла проверку качества",
            }
        if (
            manifest.get("contract_version") != CONTRACT_VERSION
            or manifest.get("features") != spec.features
            or manifest.get("targets") != spec.targets
            or manifest.get("crop_name") != spec.crop_name
            or manifest.get("status") != "validated"
            or manifest.get("training_data_reviewed") is not True
        ):
            return {"status": "unavailable", "reason": "Модель не прошла проверку контракта или качества"}
        import sklearn

        if manifest.get("sklearn_version") != sklearn.__version__:
            return {"status": "unavailable", "reason": "Версия scikit-learn отличается от версии обучения"}
        for target in spec.targets:
            metrics = manifest["test_metrics"][target]
            if not (metrics["r2"] > 0 and metrics["mae"] < metrics["baseline_mae"] * 0.9):
                return {"status": "unavailable", "reason": "Метрики не проходят порог качества"}
        file = MODELS / manifest["artifact"]
        if file.parent != MODELS or hashlib.sha256(file.read_bytes()).hexdigest() != manifest["sha256"]:
            return {"status": "unavailable", "reason": "Артефакт модели не соответствует манифесту"}
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "reason": "Ошибка чтения модели"}


@lru_cache(maxsize=4)
def load_bundle(filename: str, digest: str):
    import joblib

    return joblib.load(MODELS / filename)


def predict(spec, values, system_type):
    status = model_status(spec)
    if status["status"] != "validated":
        return status
    if system_type not in status["system_types"]:
        return {"status": "unsupported_configuration", "reason": "Модель не валидирована для этого типа системы"}
    if values is None:
        return {
            "status": "missing_inputs",
            "required": spec.features[2:],
            "reason": "Не заданы условия прогнозирования",
        }
    try:
        if any(
            not status["feature_ranges"][f][0] <= values[f] <= status["feature_ranges"][f][1] for f in spec.features
        ):
            return {"status": "out_of_domain", "reason": "Входные данные вне диапазона обучающей выборки"}
        import numpy as np
        import pandas as pd

        bundle = load_bundle(status["artifact"], status["sha256"])
        output = np.asarray(
            bundle.predict(pd.DataFrame([[values[f] for f in spec.features]], columns=spec.features))
        ).reshape(-1)
        if len(output) != len(spec.targets) or any(
            not math.isfinite(float(v)) or not spec.target_bounds[t][0] <= v <= spec.target_bounds[t][1]
            for t, v in zip(spec.targets, output)
        ):
            return {"status": "invalid_prediction", "reason": "Прогноз вне допустимых границ"}
        return {
            "status": "model_prediction",
            "values": dict(zip(spec.targets, map(float, output))),
            "units": spec.targets,
            "metrics": status["test_metrics"],
            "model_type": status["model_type"],
            "artifact_sha256": status["sha256"],
            "note": "Проверка по независимым публикациям; перенос на новый участок требует полевой валидации",
        }
    except Exception:
        # Isolated artifact boundary: a corrupt pickle must not fabricate a forecast.
        logger.exception("Unable to execute model %s", spec.name)
        return {"status": "unavailable", "reason": "Ошибка выполнения модели"}


def predict_microclimate(request) -> dict:
    if request.sector != "crop":
        return {"status": "unsupported_sector", "reason": "Модель микроклимата предназначена для растениеводства"}
    if request.coverage == 0:
        return {"status": "no_panels", "values": {k: 0 for k in MICROCLIMATE.targets}, "units": MICROCLIMATE.targets}
    values = (
        {"coverage": request.coverage, "height": request.height, **request.microclimate_inputs.model_dump()}
        if request.microclimate_inputs
        else None
    )
    return predict(MICROCLIMATE, values, request.system_type)


def predict_yield(request) -> dict:
    if request.coverage == 0:
        return {"status": "no_panels", "values": {"relative_yield_pct": 100}}
    values = (
        {"coverage": request.coverage, "height": request.height, **request.yield_inputs.model_dump()}
        if request.yield_inputs
        else None
    )
    return predict(yield_spec(request.crop_name), values, request.system_type)
