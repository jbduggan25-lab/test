"""
Bulk-download Articles of Organization from the Massachusetts Corporations Division.

Usage:
    pip install playwright
    playwright install chromium
    python scraper/scrape_articles.py entities.csv

entities.csv: the Secretary's office export, with at least the columns
"ID Number", "Entityname", "DateOfOrganization".

Outputs (all written incrementally, safe to stop and restart):
    data/pdfs/<state_id>.pdf   the Articles of Organization for each entity found
    data/output/results.csv    search_name, expected_id, state_id, filing_no,
                                status, pdf_path, note
    data/output/entities.csv   summary-page fields: ID, type, org date, principal
                                office, resident agent
    data/output/officers.csv   the summary page's Officers & Directors grid
                                (title, name, address, term) - one row per person

Status values in results.csv:
    ok            PDF saved
    unresolved    name search returned zero or several candidates; do by hand
    id_mismatch   name search landed on an entity whose displayed ID doesn't
                  match the expected ID Number from entities.csv - likely a
                  duplicate/reused name; PDF is NOT downloaded, needs a human
    no_articles   entity found but no Articles of Organization row listed
    no_pdf        row found but the PDF request didn't return a PDF
    error         unexpected exception (see note / debug screenshot)

What has been confirmed from page source vs. what is still inferred:
    CONFIRMED  CorpSummary.aspx  - all element IDs used in parse_summary(),
               the filing select (#MainContent_lstFilings, value 0300013 =
               Articles of Organization) and #MainContent_btnViewFilings
    CONFIRMED  CorpSearchFormList.aspx - #MainContent_grdSearchResults rows,
               column order, and the CorpSearchRedirector.aspx?Action=PDF link
    INFERRED   CorpSearch.aspx - the entity-name text box and the layout of the
               results grid. Marked ### VERIFY ### below. The script pauses
               with the browser open if these fail so you can click through.

Etiquette / robots.txt: the site's robots rules disallow automated access to
the summary/list pages this script depends on. This runs one entity at a time
with a delay to stay as light as possible, but that does not make it
robots-compliant - read the Division's terms and consider asking them for a
bulk data extract before running this across a full multi-year list. Keep
batches small.

Launches a visible (non-headless) browser window. On a server with no
display, run under Xvfb: `xvfb-run -a python scraper/scrape_articles.py entities.csv`
"""

import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SEARCH_URL = "https://corp.sec.state.ma.us/corpweb/CorpSearch/CorpSearch.aspx"
ARTICLES_CODE = "0300013"     # value of "Articles of Organization" in the filings select
PDF_DIR = Path("data/pdfs")
RESULTS = Path("data/output/results.csv")
ENTITIES = Path("data/output/entities.csv")
OFFICERS = Path("data/output/officers.csv")
DEBUG_DIR = Path("data/debug")
PAUSE_BETWEEN = 2.5           # seconds between entities
PAGE_TIMEOUT = 45_000         # ms


# ----------------------------------------------------------------- helpers --

def _read_csv_rows(path):
    """csv.DictReader, but skips leading all-blank rows first (Excel exports
    sometimes have a blank row before the real header, which would otherwise
    get read as the header itself, silently emptying every column name)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        raw_rows = list(csv.reader(f))
    while raw_rows and not any(cell.strip() for cell in raw_rows[0]):
        raw_rows.pop(0)
    if not raw_rows:
        return
    header, data_rows = raw_rows[0], raw_rows[1:]
    for row in data_rows:
        yield dict(zip(header, row))


def load_input(path):
    """Read the Secretary's office export CSV. Returns a list of dicts with
    name / id_number / date_of_org, skipping rows with no name or ID."""
    rows = []
    for row in _read_csv_rows(path):
        name = (row.get("Entityname") or "").strip()
        id_number = (row.get("ID Number") or "").strip()
        if not name or not id_number:
            continue
        rows.append({
            "name": name,
            "id_number": id_number,
            "date_of_org": (row.get("DateOfOrganization") or "").strip(),
        })
    return rows


def already_done():
    if not RESULTS.exists():
        return set()
    with open(RESULTS, newline="", encoding="utf-8") as f:
        return {r["expected_id"] for r in csv.DictReader(f) if r.get("status") == "ok"}


def append_rows(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)


def log_result(search_name, expected_id, state_id, filing_no, status, pdf_path="", note=""):
    append_rows(RESULTS, [{
        "search_name": search_name, "expected_id": expected_id, "state_id": state_id,
        "filing_no": filing_no, "status": status, "pdf_path": pdf_path, "note": note,
    }])
    print(f"[{status}] {search_name}" + (f": {note}" if note else ""))


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def txt(page, selector):
    loc = page.locator(selector)
    return loc.first.inner_text().strip() if loc.count() else ""


# ------------------------------------------------------- search (inferred) --

SEARCH_MODES = {"Exact match": "M", "Begins with": "B", "Full text": "F", "Soundex": "S"}


def run_search(page, name, mode):
    """
    mode: 'Exact match' or 'Begins with'.
    Confirmed from page source: #MainContent_txtEntityName (text box),
    #MainContent_ddBeginsWithEntityName (select: B/M/F/S),
    #MainContent_ddRecordsPerPage (25/50/100), #MainContent_btnSearch
    ("Search Corporations"). Results arrive via an AJAX UpdatePanel into
    #MainContent_SearchControl_tblGrid1, so there is no page navigation to
    wait for; we wait for that container to fill instead.
    """
    page.goto(SEARCH_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_selector("#MainContent_txtEntityName", timeout=PAGE_TIMEOUT)
    page.check("#MainContent_rdoByEntityName")
    page.fill("#MainContent_txtEntityName", name)
    page.select_option("#MainContent_ddBeginsWithEntityName", SEARCH_MODES[mode])
    page.select_option("#MainContent_ddRecordsPerPage", "100")
    page.click("#MainContent_btnSearch")
    try:
        page.wait_for_function(
            """() => {
                const g = document.querySelector('#MainContent_SearchControl_tblGrid1');
                if (g && g.innerText.trim().length > 0) return true;
                if (document.querySelector('table.Grid')) return true;
                const m = document.querySelector('#MainContent_lblMessage');
                return !!(m && m.innerText.trim().length > 0);
            }""",
            timeout=PAGE_TIMEOUT,
        )
    except PWTimeout:
        pass                      # fall through; pick_result will report "no results"
    page.wait_for_timeout(500)


def pick_result(page, name):
    """Return (link locator, note) for the single matching entity, or (None, note)."""
    ### VERIFY ###  results grid layout; assumes one <tr> per entity with the
    # name as a link and the entity type somewhere in the row text.
    rows = page.locator("table.Grid tr.GridRow")
    if rows.count() == 0:
        rows = page.locator("table tr").filter(has=page.locator("a"))
    n = rows.count()
    if n == 0:
        return None, "no results"
    target = norm(name)
    exact, nonprofit_exact = [], []
    for i in range(n):
        row = rows.nth(i)
        link = row.locator("a").first
        if link.count() == 0:
            continue
        if norm(link.inner_text()) == target:
            exact.append(link)
            if "nonprofit" in row.inner_text().lower():
                nonprofit_exact.append(link)
    if len(nonprofit_exact) == 1:
        return nonprofit_exact[0], ""
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        return None, f"{len(exact)} exact-name matches"
    if n == 1:
        return rows.nth(0).locator("a").first, "single non-exact result"
    return None, f"{n} results, none exact"


# ------------------------------------------------- summary page (confirmed) --

def parse_summary(page):
    d = {
        "state_id": txt(page, "#MainContent_lblIDNumberHeader"),
        "entity_name": txt(page, "#MainContent_lblEntityName"),
        "entity_type": txt(page, "#MainContent_lblEntityType"),
        "org_date": txt(page, "#MainContent_lblOrganisationDate"),
        "principal_street": txt(page, "#MainContent_lblPrincipleStreet"),
        "principal_city": txt(page, "#MainContent_lblPrincipleCity").rstrip(", "),
        "principal_state": txt(page, "#MainContent_lblPrincipleState"),
        "principal_zip": txt(page, "#MainContent_lblPrincipleZip"),
        "agent_name": txt(page, "#MainContent_lblResidentAgentName"),
        "agent_street": txt(page, "#MainContent_lblResidentStreet"),
        "agent_city": txt(page, "#MainContent_lblResidentCity").rstrip(", "),
        "agent_state": txt(page, "#MainContent_lblResidentState"),
        "agent_zip": txt(page, "#MainContent_lblResidentZip"),
        "summary_url": page.url,
    }
    officers = []
    rows = page.locator("#MainContent_grdOfficers tr.GridRow")
    for i in range(rows.count()):
        c = rows.nth(i).locator("td")
        if c.count() >= 4:
            officers.append({
                "state_id": d["state_id"],
                "title": c.nth(0).inner_text().strip(),
                "name": re.sub(r"\s+", " ", c.nth(1).inner_text()).strip(),
                "address": re.sub(r"\s+", " ", c.nth(2).inner_text()).strip(),
                "term_expires": c.nth(3).inner_text().strip(),
            })
    return d, officers


# ------------------------------------------------- filing list (confirmed) --

def find_articles_pdf(page, state_id="", debug_dir=None):
    """
    On CorpSummary.aspx: select Articles of Organization, click View filings,
    then on CorpSearchFormList.aspx find the row and return
    (absolute pdf url, filing number, note).
    """
    # Select by visible label rather than the hardcoded value, since the
    # internal option value may differ by entity type (nonprofit vs.
    # business corporation) even though the label text is the same.
    try:
        page.select_option("#MainContent_lstFilings", label=re.compile(r"articles of organization", re.I))
    except Exception:
        page.select_option("#MainContent_lstFilings", ARTICLES_CODE)
    page.click("#MainContent_btnViewFilings")
    page.wait_for_url(re.compile(r"CorpSearchFormList", re.I), timeout=PAGE_TIMEOUT)
    page.wait_for_selector("#MainContent_grdSearchResults", timeout=PAGE_TIMEOUT)

    rows = page.locator("#MainContent_grdSearchResults tr.GridRow")
    candidates = []
    seen_names = []
    for i in range(rows.count()):
        # The site renders the leading checkbox cell as <th scope="row">
        # instead of <td> when an entity has exactly one filing on record
        # (e.g. a new entity with only its original Articles of Organization
        # and no annual reports yet) - match both so column positions stay
        # correct either way.
        c = rows.nth(i).locator("td, th")
        if c.count() < 6:
            continue
        filing_name = c.nth(1).inner_text().strip()
        seen_names.append(filing_name)
        if "articles of organization" not in filing_name.lower():
            continue
        date_s = c.nth(3).inner_text().strip()
        filing_no = c.nth(4).inner_text().strip()
        a = c.nth(5).locator("a")
        if a.count() == 0:
            continue
        href = a.first.get_attribute("href") or ""
        try:
            when = datetime.strptime(date_s, "%m/%d/%Y %I:%M %p")
        except ValueError:
            when = datetime.max
        candidates.append((when, filing_no, urljoin(page.url, href), a.first.inner_text().strip()))

    if not candidates:
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(debug_dir / f"{state_id or 'unknown'}_filinglist.png"), full_page=True)
                (debug_dir / f"{state_id or 'unknown'}_filinglist.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
        if seen_names:
            note = f"no Articles of Organization row; filing types seen: {sorted(set(seen_names))}"
        else:
            note = "no Articles of Organization row; grid had no rows at all"
        return None, "", note
    candidates.sort()                       # earliest filing first
    when, filing_no, url, label = candidates[0]
    note = f"{len(candidates)} articles rows; took earliest" if len(candidates) > 1 else label
    return url, filing_no, note


def fetch_pdf(context, url, _depth=0):
    """
    GET the redirector link with the browser's cookies (redirects are followed,
    landing on CorpSearchViewPDF.aspx). Return PDF bytes or None.
    If the viewer returns HTML that embeds the PDF, follow the embed once.
    """
    resp = context.request.get(url, timeout=PAGE_TIMEOUT)
    body = resp.body()
    if resp.ok and body[:5] == b"%PDF-":
        return body
    if _depth == 0 and resp.ok and b"<" in body[:200]:
        html = body.decode("utf-8", "ignore")
        m = re.search(r"<(?:iframe|embed|object)[^>]+(?:src|data)=[\"']?([^\"' >]+)", html, re.I)
        if m:
            return fetch_pdf(context, urljoin(resp.url, m.group(1)), _depth=1)
    return None


# --------------------------------------------------------------------- main --

def main(csv_path):
    entries = load_input(csv_path)
    done = already_done()
    todo = [e for e in entries if e["id_number"] not in done]
    print(f"{len(entries)} entities, {len(done)} already done, {len(todo)} to go")
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        for entry in todo:
            name, expected_id = entry["name"], entry["id_number"]
            state_id, filing_no = "", ""
            try:
                # 1. search
                run_search(page, name, "Exact match")
                link, note = pick_result(page, name)
                if link is None and note == "no results":
                    run_search(page, name, "Begins with")
                    link, note = pick_result(page, name)
                if link is None:
                    log_result(name, expected_id, "", "", "unresolved", note=note)
                    time.sleep(PAUSE_BETWEEN)
                    continue

                # 2. summary page
                link.click()
                page.wait_for_url(re.compile(r"CorpSummary", re.I), timeout=PAGE_TIMEOUT)
                page.wait_for_selector("#MainContent_lblIDNumberHeader", timeout=PAGE_TIMEOUT)
                summary, officers = parse_summary(page)
                state_id = summary["state_id"]
                if not state_id:
                    print(f"Couldn't read ID for {name}; pausing so you can look. Press Resume when on the summary page.")
                    page.pause()
                    summary, officers = parse_summary(page)
                    state_id = summary["state_id"] or norm(name)[:40]

                # Cross-check against the ID number already known from the
                # Secretary's export - catches a name-search false match
                # (e.g. a dissolved entity whose name was later reused).
                if norm(state_id).lstrip("0") != norm(expected_id).lstrip("0"):
                    log_result(name, expected_id, state_id, "", "id_mismatch",
                               note=f"searched for {name}, landed on ID {state_id}, expected {expected_id}")
                    time.sleep(PAUSE_BETWEEN)
                    continue

                summary["search_name"] = name
                append_rows(ENTITIES, [summary])
                append_rows(OFFICERS, officers)

                # 3. filing list -> pdf link
                url, filing_no, note = find_articles_pdf(page, state_id, DEBUG_DIR)
                if url is None:
                    log_result(name, expected_id, state_id, "", "no_articles", note=note)
                    time.sleep(PAUSE_BETWEEN)
                    continue

                # 4. download
                pdf = fetch_pdf(context, url)
                if pdf is None:
                    # Fallback: click the link and catch the PDF response.
                    caught = {}
                    def on_resp(r):
                        if "application/pdf" in r.headers.get("content-type", ""):
                            try:
                                caught["pdf"] = r.body()
                            except Exception:
                                pass
                    context.on("response", on_resp)
                    with context.expect_page(timeout=10_000) as pop:
                        page.locator(f"a[href*='{filing_no}']").first.click()
                    tab = pop.value
                    deadline = time.time() + 20
                    while "pdf" not in caught and time.time() < deadline:
                        time.sleep(0.5)
                    context.remove_listener("response", on_resp)
                    tab.close()
                    pdf = caught.get("pdf")
                if pdf is None:
                    log_result(name, expected_id, state_id, filing_no, "no_pdf", note=url)
                    time.sleep(PAUSE_BETWEEN)
                    continue

                out = PDF_DIR / f"{state_id}.pdf"
                out.write_bytes(pdf)
                log_result(name, expected_id, state_id, filing_no, "ok", str(out), note)

            except Exception as e:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                shot = DEBUG_DIR / (re.sub(r"[^A-Za-z0-9]+", "_", name)[:60] + ".png")
                try:
                    page.screenshot(path=str(shot), full_page=True)
                except Exception:
                    pass
                log_result(name, expected_id, state_id, filing_no, "error",
                           note=f"{type(e).__name__}: {str(e).splitlines()[0][:150]} (see {shot})")
            time.sleep(PAUSE_BETWEEN)

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
