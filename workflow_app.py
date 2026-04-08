#!/usr/bin/env python3
"""
Local browser UI for the NY Post workflow: pick a team member and a step, run the matching script.

Bind defaults to 127.0.0.1 only. Do not expose this app to the public internet without
authentication and a proper deployment setup.

Usage (from project root):

  python workflow_app.py

Dependencies from ``requirements.txt`` are installed automatically on startup (same
Python as this process). If ``pip`` is missing, ``python -m ensurepip`` is tried.
To skip: ``NYPOST_WORKFLOW_SKIP_PIP=1``. Then your browser
should open http://127.0.0.1:5050 automatically (or open it manually).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESULTS = BASE_DIR / "results"
JOBS_DIR = RESULTS / "jobs"
REQUIREMENTS_TXT = BASE_DIR / "requirements.txt"


def _pip_available() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return r.returncode == 0


def _bootstrap_pip_with_ensurepip() -> bool:
    """Try to install pip into this interpreter (stdlib ``ensurepip``). May fail if disabled (e.g. some Linux packages)."""
    print("pip is not available; trying python -m ensurepip …", file=sys.stderr)
    proc = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
        cwd=str(BASE_DIR),
        stdout=sys.stderr,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode == 0:
        return True
    proc = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        cwd=str(BASE_DIR),
        stdout=sys.stderr,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode == 0


def _print_pip_missing_help() -> None:
    exe = sys.executable
    print(
        "\n"
        "pip is not installed for this Python and could not be added automatically.\n"
        "Options:\n"
        f"  • Install pip for this interpreter:  {exe} -m ensurepip\n"
        "  • Linux (Debian/Ubuntu):  sudo apt install python3-pip   (use your distro’s Python package)\n"
        "  • Or install from: https://pip.pypa.io/en/stable/installation/\n"
        "  • Then run:  pip install -r requirements.txt\n"
        "  • Or skip auto-install and set NYPOST_WORKFLOW_SKIP_PIP=1 if you install deps another way.\n",
        file=sys.stderr,
    )


def ensure_dependencies() -> None:
    """Install packages from requirements.txt before third-party imports (e.g. Flask)."""
    if os.environ.get("NYPOST_WORKFLOW_SKIP_PIP"):
        return
    if not REQUIREMENTS_TXT.is_file():
        return
    if not _pip_available():
        if not _bootstrap_pip_with_ensurepip() or not _pip_available():
            _print_pip_missing_help()
            return
    print("Installing dependencies from requirements.txt …", file=sys.stderr)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(REQUIREMENTS_TXT),
        ],
        cwd=str(BASE_DIR),
        stdout=sys.stderr,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        print(
            "Warning: pip install failed; try manually: pip install -r requirements.txt",
            file=sys.stderr,
        )


ensure_dependencies()

try:
    from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
except ImportError as e:
    print(
        f"\nFlask is not installed ({e}). Install dependencies first, for example:\n"
        f"  {sys.executable} -m pip install -r requirements.txt\n"
        "If pip is missing, see the messages above or https://pip.pypa.io/en/stable/installation/\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

# Year ranges for the web UI name dropdown (edit to match your project).
TEAM: dict[str, dict] = {
    "Salena": {"slug": "salena", "start": 1999, "end": 2002},
    "Joice": {"slug": "joice", "start": 2003, "end": 2005},
    "Bhuwan": {"slug": "bhuwan", "start": 2006, "end": 2008},
    "Addis": {"slug": "addis", "start": 2009, "end": 2011},
    "Shiyu": {"slug": "shiyu", "start": 2012, "end": 2015},
    "Farnoosh": {"slug": "farnoosh", "start": 2016, "end": 2019},
    "Carey": {"slug": "carey", "start": 2020, "end": 2022},
}

WORKFLOWS = {
    "all": "Run steps 1, 2, and 3 in order",
    "fetch": "Step 1: Fetch URLs from the archive",
    "scrape": "Step 2: Scrape articles (download pages)",
    "clean": "Step 3: Clean the scraped CSV",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024

# job_id -> {"status": "running" | "done" | "failed", "error": str | None}
JOB_STATE: dict[str, dict] = {}

# job_id -> {"person", "workflow", "slug", "y0", "y1"} for progress estimates
JOB_META: dict[str, dict] = {}


def job_meta_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.meta.json"


def job_state_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.jobstate.json"


def save_job_meta(job_id: str, meta: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_meta_path(job_id).write_text(
        json.dumps(meta, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def get_job_meta(job_id: str) -> dict:
    if job_id in JOB_META:
        return JOB_META[job_id]
    path = job_meta_path(job_id)
    if path.is_file():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                JOB_META[job_id] = meta
                return meta
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def persist_job_state_disk(job_id: str) -> None:
    st = JOB_STATE.get(job_id)
    if not st:
        return
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_state_path(job_id).write_text(
        json.dumps(st, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def set_job_state(job_id: str, status: str, error: str | None = None) -> None:
    JOB_STATE[job_id] = {"status": status, "error": error}
    persist_job_state_disk(job_id)


def load_job_state(job_id: str) -> dict:
    if job_id in JOB_STATE:
        return JOB_STATE[job_id]
    path = job_state_path(job_id)
    if path.is_file():
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(st, dict) and "status" in st:
                JOB_STATE[job_id] = st
                return st
        except (OSError, json.JSONDecodeError):
            pass
    return {"status": "running", "error": None}


def total_months_in_range(y0: int, y1: int) -> int:
    """Full January through December for each year in [y0, y1]."""
    return max(0, (y1 - y0 + 1) * 12)


def count_csv_data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f)
        return max(0, n - 1)
    except OSError:
        return 0


def compute_progress(log_text: str, meta: dict) -> tuple[float | None, str]:
    """
    Return (percent 0 to 100, or None if indeterminate, status label for the bar).
    """
    wf = meta.get("workflow", "")
    slug = meta.get("slug", "")
    y0 = int(meta.get("y0", 0))
    y1 = int(meta.get("y1", 0))
    total_m = total_months_in_range(y0, y1)
    month_markers = len(re.findall(r"\[INFO\] month \d{4}-\d{2}", log_text))

    p = paths_for(slug)
    state_days = fetch_resume_day_count(slug)

    if wf == "fetch":
        if total_m <= 0:
            return 0.0, "Starting fetch…"
        pct = min(100.0, 100.0 * month_markers / total_m)
        if month_markers == 0 and state_days > 0:
            pct = max(pct, 5.0)
        label = f"Fetch: {month_markers} of {total_m} months"
        if state_days > 0:
            label += f" · {state_days} day pages in resume file"
        return pct, label

    if wf == "scrape":
        total_t = count_csv_data_rows(p["targets"])
        batches = [int(x) for x in re.findall(r"\[SCRAPE\] done=(\d+)", log_text)]
        done_batch = batches[-1] if batches else 0
        if re.search(r"\[SCRAPE_DONE\]", log_text):
            return 100.0, "Scrape finished"
        if total_t <= 0:
            return None, f"Scrape: {done_batch} tasks (building list…)"
        pct = min(100.0, 100.0 * done_batch / total_t)
        return pct, f"Scrape: {done_batch} of {total_t} URLs"

    if wf == "clean":
        if re.search(r"^\s*Done\.\s*$", log_text, re.MULTILINE) and "Wrote:" in log_text:
            return 100.0, "Clean finished"
        return None, "Cleaning CSV…"

    if wf == "all":
        has_scrape = "========== SCRAPE ==========" in log_text
        has_clean = "========== CLEAN ==========" in log_text
        if not has_scrape:
            if total_m <= 0:
                return 0.0, "Starting…"
            inner = 100.0 * month_markers / total_m
            if month_markers == 0 and state_days > 0:
                inner = max(inner, 5.0)
            pct = min(40.0, 40.0 * inner / 100.0)
            label = f"Step 1 fetch: {month_markers} of {total_m} months"
            if state_days > 0:
                label += f" · {state_days} day pages in resume file"
            return pct, label
        if not has_clean:
            total_t = count_csv_data_rows(p["targets"])
            batches = [int(x) for x in re.findall(r"\[SCRAPE\] done=(\d+)", log_text)]
            done_batch = batches[-1] if batches else 0
            if re.search(r"\[SCRAPE_DONE\]", log_text):
                return 85.0, "Step 2 done, starting clean…"
            if total_t <= 0:
                return 45.0, "Step 2 scrape: starting…"
            span = 45.0
            base = 40.0
            pct = base + min(span, span * done_batch / total_t)
            return min(85.0, pct), f"Step 2 scrape: {done_batch} of {total_t} URLs"
        if re.search(r"^\s*Done\.\s*$", log_text, re.MULTILINE) and "Wrote:" in log_text:
            return 100.0, "All steps finished"
        return 92.0, "Step 3 clean…"

    return None, "Running…"


def paths_for(slug: str):
    """Standard result paths for one team member."""
    return {
        "urls": RESULTS / f"nypost_urls_{slug}.csv",
        "targets": RESULTS / f"nypost_targets_{slug}.csv",
        "articles": RESULTS / f"nypost_articles_{slug}.csv",
        "failures": RESULTS / f"nypost_articles_{slug}_failures.csv",
        "clean": RESULTS / f"nypost_articles_{slug}_clean.csv",
    }


def fetch_resume_day_count(slug: str) -> int:
    """How many day-page URLs are already marked done in fetch state (resume file)."""
    p = paths_for(slug)["urls"]
    state_path = p.with_suffix(p.suffix + ".state.json")
    if not state_path.is_file():
        return 0
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return len(data.get("completed_days", []))
    except (OSError, json.JSONDecodeError):
        return 0


def run_step(
    log,
    script: str,
    args: list[str],
) -> int:
    cmd = [sys.executable, str(BASE_DIR / script)] + args
    log.write("$ " + " ".join(cmd) + "\n\n")
    log.flush()
    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log.write(f"\n--- exit code {proc.returncode} ---\n")
    log.flush()
    return proc.returncode


def job_worker(job_id: str, person: str, workflow: str):
    info = TEAM[person]
    slug = info["slug"]
    y0, y1 = info["start"], info["end"]
    p = paths_for(slug)
    RESULTS.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOBS_DIR / f"{job_id}.log"
    set_job_state(job_id, "running", None)

    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Person: {person} (years {y0} through {y1})\n")
            log.write(f"Workflow: {WORKFLOWS.get(workflow, workflow)}\n\n")

            if workflow == "fetch":
                code = run_step(
                    log,
                    "fetch_nypost_archive_urls.py",
                    [
                        "--start-year",
                        str(y0),
                        "--end-year",
                        str(y1),
                        "--output",
                        str(p["urls"]),
                    ],
                )
            elif workflow == "scrape":
                code = run_step(
                    log,
                    "nypost_scrape.py",
                    [
                        "--input-urls",
                        str(p["urls"]),
                        "--start-year",
                        str(y0),
                        "--end-year",
                        str(y1),
                        "--targets-out",
                        str(p["targets"]),
                        "--scrape-out",
                        str(p["articles"]),
                        "--failures-out",
                        str(p["failures"]),
                    ],
                )
            elif workflow == "clean":
                code = run_step(
                    log,
                    "clean_articles.py",
                    [
                        "--input",
                        str(p["articles"]),
                        "--output",
                        str(p["clean"]),
                        "--min-words",
                        "30",
                    ],
                )
            elif workflow == "all":
                for step in ("fetch", "scrape", "clean"):
                    log.write(f"\n========== {step.upper()} ==========\n\n")
                    log.flush()
                    if step == "fetch":
                        c = run_step(
                            log,
                            "fetch_nypost_archive_urls.py",
                            [
                                "--start-year",
                                str(y0),
                                "--end-year",
                                str(y1),
                                "--output",
                                str(p["urls"]),
                            ],
                        )
                    elif step == "scrape":
                        c = run_step(
                            log,
                            "nypost_scrape.py",
                            [
                                "--input-urls",
                                str(p["urls"]),
                                "--start-year",
                                str(y0),
                                "--end-year",
                                str(y1),
                                "--targets-out",
                                str(p["targets"]),
                                "--scrape-out",
                                str(p["articles"]),
                                "--failures-out",
                                str(p["failures"]),
                            ],
                        )
                    else:
                        c = run_step(
                            log,
                            "clean_articles.py",
                            [
                                "--input",
                                str(p["articles"]),
                                "--output",
                                str(p["clean"]),
                                "--min-words",
                                "30",
                            ],
                        )
                    if c != 0:
                        set_job_state(job_id, "failed", f"Step {step} exited with {c}")
                        return
                code = 0
            else:
                log.write("Unknown workflow.\n")
                code = 1

            if workflow != "all":
                if code != 0:
                    set_job_state(job_id, "failed", f"Exit code {code}")
                    return
                set_job_state(job_id, "done", None)
            else:
                set_job_state(job_id, "done", None)
    except Exception as e:
        set_job_state(job_id, "failed", str(e))
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n[exception] {e}\n")


def team_rows():
    """Name + year range for dropdown labels."""
    return [
        {
            "name": n,
            "years": f"{d['start']} to {d['end']}",
            "y0": d["start"],
            "y1": d["end"],
        }
        for n, d in sorted(TEAM.items(), key=lambda x: x[0])
    ]


@app.route("/")
def index():
    return render_template(
        "workflow.html",
        team_rows=team_rows(),
        workflow_choices=list(WORKFLOWS.items()),
    )


@app.post("/run")
def run_workflow():
    person = request.form.get("person", "").strip()
    workflow = request.form.get("workflow", "").strip()
    if person not in TEAM or workflow not in WORKFLOWS:
        abort(400)
    job_id = uuid.uuid4().hex[:16]
    info = TEAM[person]
    JOB_META[job_id] = {
        "person": person,
        "workflow": workflow,
        "slug": info["slug"],
        "y0": info["start"],
        "y1": info["end"],
    }
    save_job_meta(job_id, JOB_META[job_id])
    t = threading.Thread(
        target=job_worker,
        args=(job_id, person, workflow),
        daemon=True,
    )
    t.start()
    return redirect(url_for("job_status", job_id=job_id))


@app.route("/status/<job_id>")
def job_status(job_id: str):
    if not job_id.isalnum() or len(job_id) > 32:
        abort(404)
    log_path = JOBS_DIR / f"{job_id}.log"
    state = load_job_state(job_id)
    log_text = ""
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    meta = get_job_meta(job_id)
    workflow_display = WORKFLOWS.get(meta.get("workflow", ""), "")
    return render_template(
        "job_status.html",
        job_id=job_id,
        log_text=log_text,
        status=state["status"],
        error=state.get("error"),
        meta=meta,
        workflow_display=workflow_display,
    )


@app.route("/api/job/<job_id>/progress")
def job_progress(job_id: str):
    if not job_id.isalnum() or len(job_id) > 32:
        abort(404)
    log_path = JOBS_DIR / f"{job_id}.log"
    state = load_job_state(job_id)
    log_text = ""
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    meta = get_job_meta(job_id)
    if meta:
        pct, label = compute_progress(log_text, meta)
    else:
        pct, label = None, "Progress unavailable (open a new run from the home page)"
    if state["status"] == "done" and pct is not None and pct < 100:
        pct = 100.0
    out = {
        "status": state["status"],
        "error": state.get("error"),
        "percent": pct,
        "label": label,
        "log": log_text,
    }
    if meta:
        out["person"] = meta.get("person")
        out["year_start"] = meta.get("y0")
        out["year_end"] = meta.get("y1")
        out["workflow"] = meta.get("workflow")
        out["workflow_label"] = WORKFLOWS.get(meta.get("workflow", ""), "")
    return jsonify(out)


def _open_browser_when_ready(url: str, delay_s: float = 1.0) -> None:
    def _run() -> None:
        time.sleep(delay_s)
        webbrowser.open(url)

    threading.Thread(target=_run, daemon=True).start()


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    url = "http://127.0.0.1:5050"
    _open_browser_when_ready(url)
    print(f"Opening {url} in your browser (only on this machine)", file=sys.stderr)
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)


if __name__ == "__main__":
    main()
