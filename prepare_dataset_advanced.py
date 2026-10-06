"""Compatibility entry point for conservative source extraction."""

import json

from agrivoltaic.preparation import prepare_dataset

if __name__ == "__main__":
    print(json.dumps(prepare_dataset(), ensure_ascii=False, indent=2))
