"""Grouped validation with fold-local scaling and an untouched test partition."""

import hashlib
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import TransformedTargetRegressor
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import DATASET, MODELS
from .ingestion import atomic_json
from .model_contract import CONTRACT_VERSION, MICROCLIMATE, yield_spec


def estimator(kind):
    if kind == "random_forest":
        model = RandomForestRegressor(n_estimators=160, max_depth=8, min_samples_leaf=3, random_state=42, n_jobs=1)
    else:
        model = MLPRegressor(hidden_layer_sizes=(32, 16), solver="lbfgs", alpha=1, max_iter=2000, random_state=42)
    return TransformedTargetRegressor(regressor=make_pipeline(StandardScaler(), model), transformer=StandardScaler())


def validate_observations(frame, spec=MICROCLIMATE):
    required = [*spec.features, *spec.targets, "study_id", "source_doi", "reviewed", "system_type"]
    if not set(required).issubset(frame.columns):
        raise ValueError(f"Необходимы колонки: {required}")
    if not frame["reviewed"].astype(str).str.lower().eq("true").all():
        raise ValueError("Обучение допускается только на проверенных наблюдениях reviewed=true")
    if frame[["study_id", "source_doi"]].isna().any().any():
        raise ValueError("Каждой строке нужны study_id и source_doi")
    if not frame["source_doi"].str.match(r"^(https://doi.org/)?10\.\d{4,9}/\S+$", na=False).all():
        raise ValueError("Некорректный source_doi")
    if frame["system_type"].nunique() != 1 or not frame["system_type"].isin(["fixed", "tracking"]).all():
        raise ValueError("Один артефакт обучается для одного типа системы: fixed либо tracking")
    if spec.crop_name is not None and ("crop_name" not in frame or not frame["crop_name"].eq(spec.crop_name).all()):
        raise ValueError("Датасет должен содержать только выбранную культуру с точным crop_name")
    numeric = frame[[*spec.features, *spec.targets]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Пропуски или бесконечные значения в обучающих измерениях")
    for feature, bounds in zip(spec.features, spec.feature_bounds):
        if not numeric[feature].between(*bounds).all():
            raise ValueError(f"Недопустимый диапазон: {feature}")
    for target, bounds in spec.target_bounds.items():
        if not numeric[target].between(*bounds).all():
            raise ValueError(f"Недопустимый диапазон: {target}")
    # All rows from a publication stay together, including multiple study_id values.
    dois = frame["source_doi"].str.lower().str.removeprefix("https://doi.org/")
    # Link publications sharing a study/site identifier to avoid cross-paper leakage.
    parents = {doi: doi for doi in dois}

    def root(doi):
        while parents[doi] != doi:
            parents[doi] = parents[parents[doi]]
            doi = parents[doi]
        return doi

    first_doi = {}
    for study, doi in zip(frame["study_id"].astype(str).str.strip(), dois):
        if not study:
            raise ValueError("Пустой study_id")
        if study in first_doi:
            a, b = root(doi), root(first_doi[study])
            parents[max(a, b)] = min(a, b)
        else:
            first_doi[study] = doi
    groups = dois.map(root)
    if len(frame) < 60 or groups.nunique() < 10:
        raise ValueError("Нужно не менее 60 проверенных наблюдений и 10 независимых публикаций")
    if any(numeric[t].nunique() < 3 for t in spec.targets):
        raise ValueError("Недостаточная вариативность целевых показателей")
    return numeric[spec.features], numeric[list(spec.targets)], groups


def train(path: Path = DATASET / "microclimate_reviewed.csv", output: Path = MODELS, spec=MICROCLIMATE) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    base = {
        "contract_version": CONTRACT_VERSION,
        "model_name": spec.name,
        "crop_name": spec.crop_name,
        "sklearn_version": sklearn.__version__,
        "features": spec.features,
        "targets": spec.targets,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if not path.exists():
            raise ValueError("Нет проверенного набора наблюдений. Заполните шаблон reviewed по первоисточникам.")
        frame = pd.read_csv(path)
        X, y, groups = validate_observations(frame, spec)
    except (OSError, ValueError) as exc:
        report = {**base, "status": "insufficient_data", "reason": str(exc)}
        atomic_json(output / f"{spec.name}_manifest.json", report)
        return report
    train_idx, test_idx = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(X, y, groups))
    Xtrain, ytrain = X.iloc[train_idx], y.iloc[train_idx]
    Xtest, ytest = X.iloc[test_idx], y.iloc[test_idx]
    training_groups = groups.iloc[train_idx]
    cv_scores = {}
    for kind in ("neural_network", "random_forest"):
        fold_scores = []
        try:
            for fit_idx, val_idx in GroupKFold(n_splits=3).split(Xtrain, ytrain, training_groups):
                model = estimator(kind)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", ConvergenceWarning)
                    model.fit(Xtrain.iloc[fit_idx], ytrain.iloc[fit_idx])
                pred = model.predict(Xtrain.iloc[val_idx])
                dummy = DummyRegressor().fit(Xtrain.iloc[fit_idx], ytrain.iloc[fit_idx])
                baseline = mean_absolute_error(
                    ytrain.iloc[val_idx], dummy.predict(Xtrain.iloc[val_idx]), multioutput="raw_values"
                )
                mae = mean_absolute_error(ytrain.iloc[val_idx], pred, multioutput="raw_values")
                fold_scores.append(float(np.mean(mae / np.maximum(baseline, 1e-9))))
            cv_scores[kind] = float(np.mean(fold_scores))
        except ConvergenceWarning:
            continue
    if not cv_scores:
        report = {**base, "status": "rejected", "reason": "Ни один кандидат не прошёл обучение"}
        atomic_json(output / f"{spec.name}_manifest.json", report)
        return report
    kind = min(cv_scores, key=cv_scores.get)
    model = estimator(kind)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(Xtrain, ytrain)
    except ConvergenceWarning:
        report = {**base, "status": "rejected", "reason": "Итоговая модель не сошлась"}
        atomic_json(output / f"{spec.name}_manifest.json", report)
        return report
    predictions = model.predict(Xtest)
    dummy = DummyRegressor().fit(Xtrain, ytrain)
    baseline_mae = mean_absolute_error(ytest, dummy.predict(Xtest), multioutput="raw_values")
    mae = mean_absolute_error(ytest, predictions, multioutput="raw_values")
    r2 = r2_score(ytest, predictions, multioutput="raw_values", force_finite=False)
    accepted = bool(np.isfinite(r2).all() and (r2 > 0).all() and (mae < baseline_mae * 0.9).all())
    report = {
        **base,
        "status": "validated" if accepted else "rejected",
        "model_type": kind,
        "training_data_reviewed": True,
        "training_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "cv_relative_mae": cv_scores,
        "test_metrics": {
            t: {
                "mae": float(mae[i]),
                "r2": float(r2[i]) if np.isfinite(r2[i]) else None,
                "baseline_mae": float(baseline_mae[i]),
            }
            for i, t in enumerate(spec.targets)
        },
        "train_studies": sorted(set(groups.iloc[train_idx])),
        "test_studies": sorted(set(groups.iloc[test_idx])),
        "training_rows": len(train_idx),
        "test_rows": len(test_idx),
        "system_types": sorted(frame["system_type"].unique()),
        "feature_ranges": {f: [float(Xtrain[f].min()), float(Xtrain[f].max())] for f in spec.features},
    }
    if accepted:
        artifact = output / f"{spec.name}_candidate.joblib"
        joblib.dump(model, artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        name = f"{spec.name}_{digest[:16]}.joblib"
        artifact.replace(output / name)
        report.update(artifact=name, sha256=digest)
    atomic_json(output / f"{spec.name}_manifest.json", report)
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATASET / "microclimate_reviewed.csv")
    parser.add_argument("--crop", help="Обучить отдельную модель относительной урожайности этой культуры")
    args = parser.parse_args()
    result = train(args.data, spec=yield_spec(args.crop) if args.crop else MICROCLIMATE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "validated" else 2)


if __name__ == "__main__":
    main()
