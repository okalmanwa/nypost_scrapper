#!/usr/bin/env python3
"""Download nypost.com articles from a URL CSV: every dated article URL in the year range
(except paths matching ``BLOCK_RE``), then fetch in parallel.

Requires: requests, beautifulsoup4 (lxml), urllib3. The ``is_opinion`` column is present but
always empty; outlet and lean are fixed."""
from __future__ import annotations

import argparse
import csv
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ARTICLE_RE = re.compile(
    r"^https?://(?:www\.)?nypost\.com/(\d{4})/(\d{2})/(\d{2})/[^?#\s]+/?$",
    re.I,
)
BLOCK_RE = re.compile(
    r"/(sports|entertainment|fashion|style|shopping|lifestyle|page-six|pagesix|video|videos|photos)/",
    re.I,
)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
OUTLET = "New York Post"
LEAN = "right"
MIN_BODY_WORDS = 30
TARGET_FIELDS = ["url", "year", "is_opinion"]
OUTPUT_FIELDS = ["title", "year", "outlet", "lean", "content", "url", "is_opinion"]
FAIL_FIELDS = ["url", "year", "is_opinion", "error"]


def _norm_url(raw: str):
    if not raw:
        return ""
    return raw.strip().split("?")[0].split("#")[0].rstrip("/")


def make_session(retries: int):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=0.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=128, pool_maxsize=128)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def load_urls(input_csv: Path):
    """Dedupe URLs, keep dated nypost.com paths only, group by year."""
    by_year: dict[int, list[str]] = defaultdict(list)
    seen: set[str] = set()
    with input_csv.open(encoding="utf-8", errors="ignore", newline="") as f:
        for row in csv.DictReader(f):
            u = _norm_url(row.get("url") or "")
            m = ARTICLE_RE.match(u)
            if not m:
                continue
            key = u.lower()
            if key in seen:
                continue
            seen.add(key)
            by_year[int(m.group(1))].append(u)
    return by_year


def urls_allowed(urls: list[str]):
    """Same order as input; drop paths matching ``BLOCK_RE``."""
    return [u for u in urls if not BLOCK_RE.search(u.lower())]


def write_targets(
    all_urls: dict[int, list[str]],
    start_year: int,
    end_year: int,
    out_path: Path,
):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    blank = {"is_opinion": ""}
    for year in range(start_year, end_year + 1):
        for u in urls_allowed(all_urls.get(year, [])):
            rows.append({"url": u, "year": year, **blank})
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TARGET_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _collect_paragraphs(containers: list, soup: BeautifulSoup):
    seen: set[str] = set()
    parts: list[str] = []
    for node in containers:
        for p in node.find_all("p"):
            t = p.get_text(" ", strip=True)
            if not t:
                continue
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            parts.append(t)
    if len(parts) < 5:
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if not t:
                continue
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            parts.append(t)
    return parts


def extract_text(html: str):
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    elif soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(" ", strip=True)
    else:
        title = ""

    containers: list = []
    art = soup.find("article")
    if art:
        containers.append(art)
    for sel in (
        "div.entry-content",
        "div.single__content",
        "div[class*='article']",
        "section[class*='article']",
        "main",
    ):
        containers.extend(soup.select(sel))

    parts = _collect_paragraphs(containers, soup)
    body = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return title, body


def scrape(
    targets_csv: Path,
    output_csv: Path,
    failures_csv: Path,
    workers: int,
    timeout: int,
    retries: int,
):
    """Append fetched rows to the output and failure CSVs."""
    seen: set[str] = set()
    if output_csv.exists():
        with output_csv.open(encoding="utf-8", errors="ignore", newline="") as f:
            for row in csv.DictReader(f):
                u = (row.get("url") or "").strip().lower()
                if u:
                    seen.add(u)

    pending: list[dict] = []
    with targets_csv.open(encoding="utf-8", errors="ignore", newline="") as f:
        for row in csv.DictReader(f):
            u = (row.get("url") or "").strip()
            if not u or u.lower() in seen:
                continue
            pending.append(row)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    need_out_header = not output_csv.exists() or output_csv.stat().st_size == 0
    need_fail_header = not failures_csv.exists() or failures_csv.stat().st_size == 0

    lock = threading.Lock()
    out_f = output_csv.open("a", newline="", encoding="utf-8")
    fail_f = failures_csv.open("a", newline="", encoding="utf-8")
    out_w = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
    fail_w = csv.DictWriter(fail_f, fieldnames=FAIL_FIELDS)
    if need_out_header:
        out_w.writeheader()
        out_f.flush()
    if need_fail_header:
        fail_w.writeheader()
        fail_f.flush()

    def fail(u: str, year: str, err: str):
        return {"url": u, "year": year, "is_opinion": "", "error": err}

    def ok_row(title: str, year: str, body: str, u: str):
        return {
            "title": title,
            "year": year,
            "outlet": OUTLET,
            "lean": LEAN,
            "content": body,
            "url": u,
            "is_opinion": "",
        }

    def job(row: dict):
        u = row["url"]
        y = row["year"]
        session = make_session(retries)
        try:
            r = session.get(u, timeout=timeout)
            if r.status_code >= 400:
                return None, fail(u, y, f"status {r.status_code}")
            title, body = extract_text(r.text)
            if not title or len(body.split()) < MIN_BODY_WORDS:
                return None, fail(u, y, "content_too_short_or_missing")
            return ok_row(title, y, body, u), None
        except Exception as e:
            return None, fail(u, y, str(e)[:300])
        finally:
            session.close()

    done = ok = bad = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, row) for row in pending]
        for fut in as_completed(futures):
            good, err = fut.result()
            with lock:
                done += 1
                if good:
                    out_w.writerow(good)
                    out_f.flush()
                    ok += 1
                if err:
                    fail_w.writerow(err)
                    fail_f.flush()
                    bad += 1
                if done % 200 == 0:
                    print(f"[SCRAPE] done={done} ok={ok} fail={bad} | in progress", flush=True)

    out_f.close()
    fail_f.close()
    print(f"[SCRAPE_DONE] done={done} ok={ok} fail={bad} | complete")


def main():
    ap = argparse.ArgumentParser(
        description="NY Post: all URLs per year in range (except BLOCK_RE paths), then scrape in parallel.",
    )
    ap.add_argument("--input-urls", required=True, help="CSV with a url column.")
    ap.add_argument(
        "--targets-out",
        default="results/nypost_targets_2000_2025.csv",
        help="Written before scraping: url, year, blank is_opinion.",
    )
    ap.add_argument(
        "--scrape-out",
        default="results/nypost_articles_2000_2025.csv",
        help="Append scraped rows.",
    )
    ap.add_argument(
        "--failures-out",
        default="results/nypost_articles_2000_2025_failures.csv",
        help="Append failed rows.",
    )
    ap.add_argument("--start-year", type=int, default=2000)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--workers", type=int, default=96)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    by_year = load_urls(Path(args.input_urls))
    n = write_targets(by_year, args.start_year, args.end_year, Path(args.targets_out))
    print(f"[TARGETS] wrote {n} targets to {args.targets_out} | targets saved")
    scrape(
        Path(args.targets_out),
        Path(args.scrape_out),
        Path(args.failures_out),
        args.workers,
        args.timeout,
        args.retries,
    )


if __name__ == "__main__":
    main()
