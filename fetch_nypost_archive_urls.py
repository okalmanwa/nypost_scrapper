#!/usr/bin/env python3
"""
Collect article URLs from the New York Post site archive (month pages, then day pages).

Writes a CSV with columns url, year, source and keeps a JSON state file next to the output
so the run can be resumed after a stop. See --help for year range, months, workers, delay,
and checkpoint options.

Requires: requests, beautifulsoup4 (lxml).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MONTH_URL = "https://nypost.com/{year}/{month:02d}/"
DAY_RE = re.compile(r"^https?://(?:www\.)?nypost\.com/(\d{4})/(\d{2})/(\d{2})/?$")
ARTICLE_RE = re.compile(r"^https?://(?:www\.)?nypost\.com/(\d{4})/(\d{2})/(\d{2})/[^?#\s]+/?$")
FIELDS = ["url", "year", "source"]


def build_session(timeout_retries: int) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=timeout_retries,
        read=timeout_retries,
        connect=timeout_retries,
        backoff_factor=0.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=128, pool_maxsize=128)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def fetch_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    r = session.get(url, timeout=timeout)
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code} for {url}")
    return r.text


def month_day_urls(session: requests.Session, year: int, month: int, timeout: int) -> list[str]:
    url = MONTH_URL.format(year=year, month=month)
    try:
        html = fetch_html(session, url, timeout=timeout)
    except Exception as e:
        print(f"[WARN] month fetch failed {url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "lxml")
    out = set()
    for a in soup.find_all("a", href=True):
        h = urljoin(url, a["href"].strip())
        h = h.split("?")[0].split("#")[0].rstrip("/") + "/"
        m = DAY_RE.match(h)
        if not m:
            continue
        y, mm = int(m.group(1)), int(m.group(2))
        if y == year and mm == month:
            out.add(h)
    return sorted(out)


def day_article_urls(session: requests.Session, day_url: str, timeout: int) -> list[str]:
    html = fetch_html(session, day_url, timeout=timeout)
    soup = BeautifulSoup(html, "lxml")
    out = set()
    for a in soup.find_all("a", href=True):
        h = urljoin(day_url, a["href"].strip())
        h = h.split("?")[0].split("#")[0].rstrip("/")
        if not ARTICLE_RE.match(h):
            continue
        if DAY_RE.match(h + "/"):
            continue
        out.add(h)
    return sorted(out)


def load_existing(output_csv: Path) -> set[str]:
    seen = set()
    if not output_csv.exists():
        return seen
    with output_csv.open(encoding="utf-8", errors="ignore", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            u = (row.get("url") or "").strip()
            if u:
                seen.add(u)
    return seen


def load_state(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return set(data.get("completed_days", []))
    except Exception:
        return set()


def save_state(state_path: Path, completed_days: set[str]) -> None:
    payload = {"completed_days": sorted(completed_days), "updated_at": int(time.time())}
    state_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def append_rows(writer, rows: list[dict], fh) -> None:
    if not rows:
        return
    writer.writerows(rows)
    fh.flush()


def run(
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    delay: float,
    output_csv: str,
    workers: int,
    timeout: int,
    retries: int,
    checkpoint_every: int,
) -> int:
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = output_path.with_suffix(output_path.suffix + ".state.json")

    seen_urls = load_existing(output_path)
    completed_days = load_state(state_path)
    print(f"[RESUME] existing urls={len(seen_urls)} completed_days={len(completed_days)}", file=sys.stderr)

    session = build_session(timeout_retries=retries)
    lock = Lock()

    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
            f.flush()

        processed_since_ckpt = 0

        for year in range(start_year, end_year + 1):
            m_from = start_month if year == start_year else 1
            m_to = end_month if year == end_year else 12
            for month in range(m_from, m_to + 1):
                print(f"[INFO] month {year}-{month:02d}", file=sys.stderr)
                day_urls_raw = month_day_urls(session, year, month, timeout=timeout)
                n_raw = len(day_urls_raw)
                day_urls = [d for d in day_urls_raw if d not in completed_days]
                n_pending = len(day_urls)
                print(
                    f"  calendar has {n_raw} day links; {n_pending} still pending "
                    f"({n_raw - n_pending} already in state file)",
                    file=sys.stderr,
                )
                if n_raw == 0:
                    print(
                        "  [HINT] No day links on this month page. Site layout may have changed, "
                        "or the request failed silently.",
                        file=sys.stderr,
                    )
                elif n_pending == 0 and n_raw > 0:
                    print(
                        "  [HINT] Every day in this month is already marked done in the state file. "
                        "Nothing to fetch. To redo this month you must edit or remove the state file "
                        f"(see {state_path.name}).",
                        file=sys.stderr,
                    )
                if not day_urls:
                    continue

                def worker(day_url: str):
                    s = build_session(timeout_retries=retries)
                    try:
                        arts = day_article_urls(s, day_url, timeout=timeout)
                        return day_url, arts, None
                    except Exception as e:
                        return day_url, [], str(e)
                    finally:
                        s.close()

                with ThreadPoolExecutor(max_workers=workers) as ex:
                    fut_map = {ex.submit(worker, d): d for d in day_urls}
                    for fut in as_completed(fut_map):
                        d = fut_map[fut]
                        day_url, arts, err = fut.result()
                        if err:
                            print(f"    [WARN] {day_url} failed: {err}", file=sys.stderr)
                            continue

                        new_rows = []
                        for u in arts:
                            if u in seen_urls:
                                continue
                            m = ARTICLE_RE.match(u)
                            yy = int(m.group(1)) if m else year
                            seen_urls.add(u)
                            new_rows.append({"url": u, "year": yy, "source": "nypost_archive"})

                        with lock:
                            append_rows(writer, new_rows, f)
                            completed_days.add(day_url)
                            processed_since_ckpt += 1
                            if processed_since_ckpt >= checkpoint_every:
                                save_state(state_path, completed_days)
                                processed_since_ckpt = 0

                        print(f"    {day_url} -> {len(arts)} article links ({len(new_rows)} new)", file=sys.stderr)

                        if delay > 0:
                            time.sleep(delay)

                save_state(state_path, completed_days)

    session.close()
    save_state(state_path, completed_days)
    print(f"[DONE] total unique URLs now {len(seen_urls)} -> {output_path}", file=sys.stderr)
    print(f"[STATE] resume file -> {state_path}", file=sys.stderr)
    return len(seen_urls)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect NY Post archive URLs with append and resume.")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--start-month", type=int, default=1)
    parser.add_argument("--end-month", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=24, help="Parallel day-page workers")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--output", type=str, default="results/nypost_archive_urls_2000_2025.csv")
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("start-year must be <= end-year")
    if not 1 <= args.start_month <= 12 or not 1 <= args.end_month <= 12:
        raise SystemExit("months must be 1..12")
    if args.workers < 1:
        raise SystemExit("workers must be >= 1")

    run(
        args.start_year,
        args.end_year,
        args.start_month,
        args.end_month,
        args.delay,
        args.output,
        args.workers,
        args.timeout,
        args.retries,
        args.checkpoint_every,
    )


if __name__ == "__main__":
    main()
