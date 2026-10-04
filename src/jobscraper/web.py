"""Web GUI: run the scraper from a browser at http://127.0.0.1:8000.

Start it with::

    pip install -e ".[web]"
    jobscraper-serve            # or: python -m jobscraper.web
    jobscraper-serve --port 8080 --host 0.0.0.0   # LAN / container use

The service binds to localhost by default. Only expose it wider if you
trust your network: anyone with access can make it fetch arbitrary URLs.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import threading
import uuid

from jobscraper.boards import DISCOVERERS
from jobscraper.pipeline import run_pipeline
from jobscraper.scoring import load_profile, validate_profile
from jobscraper.sources.linkedin import search_jobs

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "The web extra is not installed. Run: pip install -e \".[web]\""
    ) from exc

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
EXAMPLES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "examples"))

app = FastAPI(title="jobscraper", version="1.0.0")

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


class LinkedInQuery(BaseModel):
    keywords: str
    location: str | None = None
    geo_id: str | None = None
    limit: int = 25
    days: int | None = None
    remote: str | None = None


class ScrapeRequest(BaseModel):
    urls: list[str] = []
    linkedin: LinkedInQuery | None = None
    discover: list[str] = []
    profile: dict | None = None
    locations: str | None = None
    location_filter: str | None = None
    keyword_filter: str | None = None
    exclude_companies: str | None = None
    exclude_keywords: str | None = None
    min_score: int | None = None
    workers: int = 4


def _resolve_urls(req: ScrapeRequest) -> list[str]:
    """Expand discover specs and LinkedIn searches into posting URLs."""
    urls = [u for u in req.urls if u.strip()]
    for spec in req.discover:
        board, _, ident = spec.partition(":")
        discover = DISCOVERERS.get(board.lower())
        if not discover:
            raise HTTPException(
                status_code=400,
                detail=f"unknown board '{board}' "
                       f"(choose from: {', '.join(sorted(DISCOVERERS))})")
        try:
            urls += discover(ident)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"discover {spec} failed: {exc}") from exc
    if req.linkedin:
        query = req.linkedin
        added, start = 0, 0
        while added < query.limit:
            cards = search_jobs(query.keywords, location=query.location,
                                geo_id=query.geo_id, start=start,
                                remote=query.remote,
                                posted_within_days=query.days)
            if not cards:
                break
            for card in cards:
                if card["url"] not in urls:
                    urls.append(card["url"])
                    added += 1
                    if added >= query.limit:
                        break
            start += 10
            if len(cards) < 10:
                break
    if not urls:
        raise HTTPException(status_code=400,
                            detail="no URLs to scrape: give urls, linkedin "
                                   "or discover")
    return urls


def _run_job(job_id: str, urls: list[str], req: ScrapeRequest) -> None:
    profile = req.profile or load_profile(None)
    if req.locations:
        profile["locations"] = [loc.strip() for loc in
                                req.locations.split(",") if loc.strip()]

    def progress(done: int, total: int) -> None:
        with _JOBS_LOCK:
            _JOBS[job_id]["done"] = done
            _JOBS[job_id]["total"] = total

    try:
        results, new_count = run_pipeline(
            urls, profile=profile, workers=req.workers,
            location_filter=req.location_filter,
            keyword_filter=req.keyword_filter,
            exclude_companies=req.exclude_companies,
            exclude_keywords=req.exclude_keywords,
            min_score=req.min_score,
            progress_cb=progress)
        payload = [p.to_dict() for p in results]
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", results=payload,
                                 profile=profile, new_count=new_count)
    except Exception as exc:
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=str(exc)[:500])


@app.post("/api/scrape")
def start_scrape(req: ScrapeRequest):
    if req.profile is not None:
        problems = validate_profile(req.profile)
        if problems:
            raise HTTPException(status_code=400,
                                detail="invalid profile: "
                                       + "; ".join(problems))
    urls = _resolve_urls(req)
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running", "done": 0,
                         "total": len(urls), "results": None,
                         "error": None, "params": req.model_dump()}
    thread = threading.Thread(target=_run_job, args=(job_id, urls, req),
                              daemon=True)
    thread.start()
    return {"job_id": job_id, "total": len(urls)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="unknown job")
        if job["status"] == "done":
            return {"status": "done", "done": job["done"],
                    "total": job["total"], "results": job["results"]}
        return {"status": job["status"], "done": job["done"],
                "total": job["total"], "error": job["error"]}


@app.get("/api/export/{job_id}.json")
def export_json(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    return JSONResponse(job["results"])


@app.get("/api/export/{job_id}.csv")
def export_csv(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["score", "live", "age_days", "title", "company",
                     "location", "url"])
    for post in job["results"]:
        match = post.get("match") or {}
        live = ("yes" if post.get("is_live") else
                "no" if post.get("is_live") is False else "unknown")
        writer.writerow([match.get("total", ""), live,
                         post.get("age_days", ""), post.get("title", ""),
                         post.get("company", ""), post.get("location", ""),
                         post.get("url", "")])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=jobscraper-{job_id}.csv"})


@app.get("/api/examples")
def list_examples():
    out = []
    for fname in sorted(os.listdir(EXAMPLES_DIR)):
        if fname.endswith(".json"):
            out.append(fname[:-5])
    return {"examples": out}


@app.get("/api/examples/{name}")
def get_example(name: str):
    path = os.path.normpath(os.path.join(EXAMPLES_DIR, name + ".json"))
    if not path.startswith(EXAMPLES_DIR) or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="unknown example")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"),
              encoding="utf-8") as fh:
        return fh.read()


def main(argv: list[str] | None = None) -> int:
    import uvicorn
    parser = argparse.ArgumentParser(prog="jobscraper-serve",
                                     description="Serve the jobscraper web GUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
