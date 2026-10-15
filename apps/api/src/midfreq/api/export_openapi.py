"""Write the API contract as OpenAPI JSON: `uv run python -m midfreq.api.export_openapi`.

apps/api/openapi.json is committed; the web app generates its TypeScript types from it, and a
test fails if the file is out of date with the code.
"""
import json
import sys
from pathlib import Path

from midfreq.api.main import app

OUT = Path(__file__).resolve().parents[3] / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    out.write_text(render())
    print(f"wrote {out}")
