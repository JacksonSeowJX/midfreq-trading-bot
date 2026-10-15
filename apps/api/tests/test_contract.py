"""apps/api/openapi.json is the contract the web app's types are generated from; keep it current."""
from midfreq.api.export_openapi import OUT, render


def test_openapi_json_is_up_to_date():
    assert OUT.read_text() == render(), \
        "apps/api/openapi.json is stale: run `uv run python -m midfreq.api.export_openapi` and `npm run gen:api` in apps/web"
