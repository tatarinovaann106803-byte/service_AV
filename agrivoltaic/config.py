"""Paths are independent of the process working directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATASET = ROOT / "dataset"
MODELS = ROOT / "models"
STATIC = ROOT / "static"
