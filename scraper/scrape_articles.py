"""
Download Articles of Organization PDFs from the Massachusetts Secretary of the
Commonwealth's Corporations Division database (corp.sec.state.ma.us) for a list
of entity ID numbers.

IMPORTANT - selectors not yet verified against the live site
--------------------------------------------------------------
This was written without live access to corp.sec.state.ma.us (network access to
that domain is blocked in the environment this was authored in). The search-box
label text, filing-table structure, and PDF-link pattern below are based on the
general shape of that ASP.NET WebForms site and are the first thing to check if
a run fails immediately for every entity.

Run a small batch first in headed + debug mode:

    python scraper/scrape_articles.py --input entities.csv --limit 3 --headed

If lookup/search/filing-matching fails, this script writes a screenshot and the
page HTML to data/debug/<id>_<step>.{png,html} instead of crashing blindly -
send those files back for a selector fix.

Usage:
    python scraper/scrape_articles.py --input entities.csv \
        --output-dir data/pdfs --log data/scrape_log.csv

Input CSV must have at least these columns (matching the Secretary's export):
    ID Number, Entityname, DateOfOrganization

Resumable: rows already marked "ok" in the log are skipped on re-run unless
--force is passed.
"""
import argparse
import csv
import random
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SEARCH_URL = "https://corp.sec.state.ma.us/corpweb/CorpSearch/CorpSearch.aspx"

LOG_FIELDS = ["id_number", "entity_name", "status", "pdf_path", "filing_date_matched", "note"]


def parse_date_of_organization(raw: str):
    raw = (raw or "").strip()
    for fmt in ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def load_log(log_path: Path):
    done = {}
    if log_path.exists():
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                done[row["id_number"]] = row
    return done


def append_log(log_path: Path, row: dict):
    new_file = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def dump_debug(page, id_number: str, step: str, debug_dir: Path):
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(debug_dir / f"{id_number}_{step}.png"), full_page=True)
    except Exception:
        pass
    try:
        (debug_dir / f"{id_number}_{step}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def search_by_id(page, id_number: str, debug_dir: Path) -> bool:
    """Navigate the search form for a given entity ID. Returns True if a
    single entity summary page was reached."""
    page.goto(SEARCH_URL, wait_until="domcontentloaded")

    # Try the common label variants for the ID-number search field.
    candidates = [
        re.compile(r"Data\s*Number", re.I),
        re.compile(r"Entity\s*ID", re.I),
        re.compile(r"ID\s*Number", re.I),
    ]
    field = None
    for pattern in candidates:
        try:
            loc = page.get_by_label(pattern)
            if loc.count() > 0:
                field = loc.first
                break
        except Exception:
            continue

    if field is None:
        dump_debug(page, id_number, "no_id_field", debug_dir)
        return False

    field.fill(id_number)
    field.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass

    # If a results LIST page loaded instead of a single summary page (e.g. the
    # ID matched more than one record, which shouldn't happen for an exact
    # Data Number match but is handled defensively), click the first result row.
    try:
        row_link = page.get_by_role("link", name=re.compile(id_number))
        if row_link.count() > 0:
            row_link.first.click()
            page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass

    return True


def find_articles_pdf_url(page, date_of_org, id_number: str, debug_dir: Path):
    """On an entity summary/filing-history page, locate the Articles of
    Organization filing and return its PDF link href (or None)."""
    try:
        page.get_by_text(re.compile(r"Filing", re.I)).first.wait_for(timeout=10000)
    except PWTimeout:
        dump_debug(page, id_number, "no_filings_section", debug_dir)
        return None

    rows = page.locator("tr").all()
    best_href = None
    best_row_text = None
    for row in rows:
        text = row.inner_text().strip()
        if not text or "articles of organization" not in text.lower():
            continue
        link = row.locator("a")
        if link.count() == 0:
            continue
        href = link.first.get_attribute("href")
        if not href:
            continue
        if date_of_org and date_of_org.strftime("%m/%d/%Y") in text:
            return href  # exact date match, take it immediately
        if best_href is None:
            best_href = href
            best_row_text = text

    if best_href is None:
        dump_debug(page, id_number, "no_articles_row", debug_dir)
    return best_href


def download_pdf(page, href: str, dest: Path) -> bool:
    url = href
    if href.startswith("javascript:") or href.startswith("#"):
        # Postback link - click it and capture the resulting download / new tab.
        try:
            with page.expect_download(timeout=20000) as dl_info:
                page.locator(f'a[href="{href}"]').first.click()
            dl_info.value.save_as(str(dest))
            return True
        except PWTimeout:
            try:
                with page.context.expect_page(timeout=10000) as new_page_info:
                    page.locator(f'a[href="{href}"]').first.click()
                new_page = new_page_info.value
                new_page.wait_for_load_state()
                resp = page.context.request.get(new_page.url)
                dest.write_bytes(resp.body())
                new_page.close()
                return True
            except Exception:
                return False
    else:
        full_url = page.url.rsplit("/", 1)[0] + "/" + url if not url.startswith("http") else url
        resp = page.context.request.get(full_url)
        if resp.ok:
            dest.write_bytes(resp.body())
            return True
        return False


def process_one(page, id_number, entity_name, date_raw, output_dir: Path, debug_dir: Path):
    date_of_org = parse_date_of_organization(date_raw)

    if not search_by_id(page, id_number, debug_dir):
        return dict(id_number=id_number, entity_name=entity_name, status="fail_search",
                    pdf_path="", filing_date_matched="", note="ID search field not found - see debug dump")

    href = find_articles_pdf_url(page, date_of_org, id_number, debug_dir)
    if not href:
        return dict(id_number=id_number, entity_name=entity_name, status="fail_no_filing",
                    pdf_path="", filing_date_matched="", note="Articles of Organization row not found")

    dest = output_dir / f"{id_number}.pdf"
    ok = download_pdf(page, href, dest)
    if not ok:
        return dict(id_number=id_number, entity_name=entity_name, status="fail_download",
                    pdf_path="", filing_date_matched="", note=f"could not fetch {href}")

    return dict(id_number=id_number, entity_name=entity_name, status="ok",
                 pdf_path=str(dest), filing_date_matched=str(date_of_org or ""), note="")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="CSV from the Secretary's office export")
    ap.add_argument("--output-dir", default="data/pdfs")
    ap.add_argument("--debug-dir", default="data/debug")
    ap.add_argument("--log", default="data/scrape_log.csv")
    ap.add_argument("--limit", type=int, default=None, help="only process the first N rows (for testing)")
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--delay", type=float, default=3.0, help="base seconds between requests")
    ap.add_argument("--jitter", type=float, default=2.0, help="max extra random seconds added to delay")
    ap.add_argument("--force", action="store_true", help="re-process rows already logged as ok")
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    debug_dir = Path(args.debug_dir)
    log_path = Path(args.log)
    output_dir.mkdir(parents=True, exist_ok=True)

    import pandas as pd
    df = pd.read_csv(args.input, dtype=str).fillna("")
    df = df.iloc[args.start_index:]
    if args.limit:
        df = df.iloc[: args.limit]

    done = load_log(log_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page()

        for _, row in df.iterrows():
            id_number = str(row.get("ID Number", "")).strip()
            entity_name = row.get("Entityname", "")
            date_raw = row.get("DateOfOrganization", "")

            if not id_number:
                continue
            if not args.force and done.get(id_number, {}).get("status") == "ok":
                print(f"skip (already ok): {id_number} {entity_name}")
                continue

            print(f"processing: {id_number} {entity_name}")
            try:
                result = process_one(page, id_number, entity_name, date_raw, output_dir, debug_dir)
            except Exception as e:
                result = dict(id_number=id_number, entity_name=entity_name, status="fail_exception",
                               pdf_path="", filing_date_matched="", note=str(e))
                dump_debug(page, id_number, "exception", debug_dir)

            print(f"  -> {result['status']} {result['note']}")
            append_log(log_path, result)
            time.sleep(args.delay + random.random() * args.jitter)

        browser.close()


if __name__ == "__main__":
    main()
