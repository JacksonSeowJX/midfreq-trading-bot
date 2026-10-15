"""
Research results in results/: the overview of every result held to the two-configuration
standard (midfreq.research.results_index), and the raw result files the research view browses.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from midfreq.api import schemas as s
from midfreq.api.convert import plain, records
from midfreq.api.deps import get_settings
from midfreq.api.labels import study_and_label
from midfreq.api.settings import Settings
from midfreq.research import results_index

log = logging.getLogger(__name__)
router = APIRouter()

KINDS = (".csv", ".json")


def result_files(results_dir: Path) -> List[Path]:
    """The CSV and JSON files directly in results/, newest first (as the research view lists them)."""
    if not results_dir.is_dir():
        return []
    files = [p for p in results_dir.iterdir() if p.is_file() and p.suffix.lower() in KINDS]
    return sorted(files, key=lambda p: (-p.stat().st_mtime, p.name))


def _search_correction(results_dir: Path) -> Optional[s.SearchCorrection]:
    try:
        sc = results_index.load_search_correction(results_dir=results_dir)
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("cannot read the Reality Check result in %s: %s", results_dir, e)
        return None
    return s.SearchCorrection(**plain(sc)) if sc else None


@router.get("/api/results/overview", response_model=s.Overview, tags=["research"])
def overview(current_only: bool = True, settings: Settings = Depends(get_settings)):
    df = results_index.load_all(current_only=current_only, results_dir=settings.results_dir)
    return s.Overview(summary=s.ResultsSummary(**plain(results_index.summary(df))),
                      rows=[s.ResultRow(**r) for r in records(df)],
                      search_correction=_search_correction(settings.results_dir))


@router.get("/api/research/files", response_model=List[s.ResearchFile], tags=["research"])
def research_files(settings: Settings = Depends(get_settings)):
    out = []
    for p in result_files(settings.results_dir):
        st = p.stat()
        study, label = study_and_label(p)
        out.append(s.ResearchFile(name=p.name, study=study, label=label, kind=p.suffix.lower()[1:],
                                  size=st.st_size,
                                  modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)))
    return out


@router.get("/api/research/files/{name}", response_model=s.ResearchFileContent, tags=["research"],
            responses={404: {"description": "no such file"}})
def research_file(name: str, limit: int = Query(5000, ge=1, le=50000),
                  settings: Settings = Depends(get_settings)):
    # Only a name from the listing is read, so no request can reach outside results/.
    path = next((p for p in result_files(settings.results_dir) if p.name == name), None)
    if path is None:
        raise HTTPException(status_code=404, detail="no such file")

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text())
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(status_code=422, detail=f"{name} is not valid JSON: {e}")
        return s.ResearchFileContent(name=name, kind="json", data=plain(data))

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        df = pd.DataFrame()
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=422, detail=f"{name} cannot be read as CSV: {e}")
    return s.ResearchFileContent(name=name, kind="csv", columns=[str(c) for c in df.columns],
                                 rows=records(df.head(limit)))
