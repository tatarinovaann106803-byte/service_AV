"""Download all three configured sources or preserve the previous snapshot."""

import json

from agrivoltaic.ingestion import refresh_sources

if __name__ == "__main__":
    print(json.dumps(refresh_sources(), ensure_ascii=False, indent=2))
