"""All-or-nothing source refresh with an atomic snapshot pointer."""

import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from .config import DATA, DATASET


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temp = Path(stream.name)
    os.replace(temp, path)


def refresh_sources(directory: Path = DATASET, fetch=None) -> dict:
    fetch = fetch or requests.get
    sources = json.loads((DATA / "sources.json").read_text())
    frames, provenance = [], []
    for source in sources:
        response = fetch(source["url"], timeout=(5, 40))
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" in content_type or content.lstrip().lower().startswith((b"<!doctype", b"<html")):
            raise ValueError(f"Вместо CSV получен HTML: {source['id']}")
        frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str, keep_default_na=False)
        if frame.empty or not set(source["required_columns"]).issubset(frame.columns):
            raise ValueError(f"Пустой файл или неверная схема источника: {source['id']}")
        frame["source_id"] = source["id"]
        frame["source_url"] = source["url"]
        frame["source_row"] = range(2, len(frame) + 2)
        frames.append(frame)
        provenance.append(
            {
                **source,
                "final_url": response.url,
                "rows": len(frame),
                "sha256": hashlib.sha256(content).hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    combined = pd.concat(frames, ignore_index=True).fillna("")
    snapshot_id = hashlib.sha256("".join(p["sha256"] for p in provenance).encode()).hexdigest()
    snapshots = directory / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    target = snapshots / snapshot_id
    if not target.exists():
        staging = Path(tempfile.mkdtemp(dir=snapshots, prefix=".staging-"))
        combined.to_csv(staging / "raw.csv", index=False)
        atomic_json(
            staging / "manifest.json",
            {
                "sources": provenance,
                "rows": len(combined),
                "raw_sha256": hashlib.sha256((staging / "raw.csv").read_bytes()).hexdigest(),
            },
        )
        os.replace(staging, target)
    # A failure above leaves the previously selected snapshot unchanged.
    pointer = {"snapshot": snapshot_id, "rows": len(combined)}
    atomic_json(directory / "current.json", pointer)
    return pointer


def current_raw(directory: Path = DATASET) -> Path:
    if not (directory / "current.json").exists():
        raise ValueError("Нет снимка исходных данных. Выполните python download_data.py")
    snapshot = json.loads((directory / "current.json").read_text())["snapshot"]
    if len(snapshot) != 64 or any(c not in "0123456789abcdef" for c in snapshot):
        raise ValueError("Некорректный идентификатор снимка")
    folder = directory / "snapshots" / snapshot
    manifest = json.loads((folder / "manifest.json").read_text())
    raw = folder / "raw.csv"
    if hashlib.sha256(raw.read_bytes()).hexdigest() != manifest["raw_sha256"]:
        raise ValueError("Контрольная сумма исходного датасета не совпадает")
    return raw
