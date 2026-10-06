"""Private server-side records; never served as static files or committed to git."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT


def private_directory():
    return Path(os.environ.get("AGRIVOLTAIC_PRIVATE_DIR", ROOT / "private")).resolve()


@contextmanager
def database():
    folder = private_directory()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "calculations.sqlite3"
    with sqlite3.connect(path, timeout=15) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS calculations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, sector TEXT NOT NULL, product TEXT NOT NULL, record TEXT NOT NULL)"
        )
        path.chmod(0o600)
        yield connection


def save_calculation(request: dict, private_result: dict) -> str:
    calculation_id = uuid.uuid4().hex
    record = {
        "id": calculation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": request,
        **private_result,
    }
    with database() as connection:
        connection.execute(
            "INSERT INTO calculations VALUES (?, ?, ?, ?, ?)",
            (
                calculation_id,
                record["created_at"],
                request["sector"],
                request["product_name"],
                json.dumps(record, ensure_ascii=False, allow_nan=False),
            ),
        )
    return calculation_id


def list_calculations(limit=100):
    with database() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id, created_at, sector, product FROM calculations ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        ]


def get_calculation(calculation_id):
    with database() as connection:
        row = connection.execute("SELECT record FROM calculations WHERE id = ?", (calculation_id,)).fetchone()
    return json.loads(row["record"]) if row else None


def select_variant(calculation_id, variant_id):
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT record FROM calculations WHERE id = ?", (calculation_id,)).fetchone()
        if row is None:
            return None
        record = json.loads(row["record"])
        if variant_id not in {variant["id"] for variant in record["variants"]}:
            raise ValueError("Вариант не принадлежит этому расчёту")
        record["selected_variant_id"] = variant_id
        record["selected_at"] = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE calculations SET record = ? WHERE id = ?",
            (json.dumps(record, ensure_ascii=False, allow_nan=False), calculation_id),
        )
    return record
