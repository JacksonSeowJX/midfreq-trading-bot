"""
The built web app (apps/web/dist), served by the API itself when it exists, for hosting both
from one process. Any path that is not a file in dist gets index.html, so the single-page app's
own routes survive a reload; /api/... is never answered from here.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def mount_web(app: FastAPI, dist: Path) -> bool:
    """Serve `dist` at / if it holds a build (index.html). Call after the API routes are added,
    so they match first. Returns whether it was mounted."""
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        return False

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            f = (root / path).resolve()
            if f.is_file() and f.is_relative_to(root):
                return FileResponse(f)
        return FileResponse(index)

    return True
