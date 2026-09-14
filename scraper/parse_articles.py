"""
Extract officers/directors and their addresses from Massachusetts c.180
Articles of Organization PDFs (the typed PDFs produced by the Corporations
Division's online filing system) and flag likely address-compliance issues
under c.156B s.13(c)(2) (name, residence, and post-office address of each
initial director and the president, treasurer, and clerk).

Usage:
    pip install pdfplumber
    python scraper/parse_articles.py data/pdfs                       # a folder of PDFs
    python scraper/parse_articles.py data/pdfs entities.csv          # + entity names for the review list
    python scraper/parse_articles.py data/pdfs/001217378.pdf         # or one file

Outputs (in data/output/):
    parsed_officers.csv   one row per person listed in Article VII(b)
    parsed_entities.csv   one row per filing, with roll-up counts and flags
    parse_log.csv         per-file status (ok / no_table / needs_ocr / error) -
                           this is your manual-review list: filter for status
                           in (needs_ocr, no_table) to get every entity whose
                           Article VII needs a human to open the PDF and read
                           it directly (pass entities.csv as the second
                           argument so this list includes entity names, not
                           just the ID-number filename)

Interpretation of the form:
    Article VII(b) is a 4-column table: Title | Individual Name |
    "Address (no PO Box)" | Expiration of Term. The address cell holds two
    stacked addresses: the residential street address first, then the
    post-office address. This script splits them on the city/state/zip line.

Flags are heuristics to prioritise review, not legal conclusions:
    res_missing        no residential address block found
    res_pobox          residential block contains a PO box / PMB
    res_co             residential block contains "c/o"
    res_eq_principal   residential address == principal office address
    res_eq_agent       residential address == resident agent's address
    res_eq_po          residential address == the person's post-office address
    res_unit           residential block mentions suite/floor/unit (weak signal)

needs_ocr in parse_log.csv means everything past the state's generic typed
cover page (page 1, present on every filing) had under 200 characters of
text - i.e. the actual filed Articles of Organization is a scanned image,
not an e-filed form with a text layer. This script does not attempt OCR;
those filings need manual review (open the PDF, read Article VII yourself)
or a separate OCR pass.

no_table means real text was found past the cover page, but no table
matching the expected Article VII(b) header ("Title" / "Name" columns) was
detected - this can mean the filing genuinely left officers/directors
blank, or that this filing's layout doesn't match what find_officer_rows()
expects. Worth a quick manual look either way.
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber

OUTPUT_DIR = Path("data/output")

REQUIRED_TITLES = {"PRESIDENT", "TREASURER", "CLERK", "DIRECTOR"}
CITY_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\b(?P<rest>.*)$")
POBOX = re.compile(r"\b(P\.?\s*O\.?\s*BOX|POST\s+OFFICE\s+BOX|PMB)\b", re.I)
CARE_OF = re.compile(r"\bC/O\b|\bCARE\s+OF\b", re.I)
UNIT = re.compile(r"\b(STE|SUITE|FL|FLOOR|UNIT|APT|#)\b", re.I)
CERTIFICATE_MARKERS = ("hereby certify", "upon examination")


def _is_certificate_page(text):
    t = text.lower()
    return all(marker in t for marker in CERTIFICATE_MARKERS)


# ------------------------------------------------------------ normalisation --

_ABBR = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
    "BOULEVARD": "BLVD", "COURT": "CT", "PLACE": "PL", "TERRACE": "TER",
    "CIRCLE": "CIR", "HIGHWAY": "HWY", "PARKWAY": "PKWY", "SQUARE": "SQ",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "SUITE": "STE", "APARTMENT": "APT", "FLOOR": "FL",
}


def norm_addr(s):
    """Uppercase, strip punctuation, standardise common suffixes, drop country."""
    s = re.sub(r"[^\w\s]", " ", s.upper())
    s = re.sub(r"\bUSA?\b|\bUNITED STATES\b", " ", s)
    toks = [_ABBR.get(t, t) for t in s.split()]
    return " ".join(toks)


# ----------------------------------------------------------------- parsing --

def split_address_cell(cell):
    """
    The address cell holds residential then post-office address, each ending
    in a 'CITY, ST 12345' line. Return (residential, po) as single strings.
    """
    lines = [l.strip() for l in (cell or "").splitlines() if l.strip()]
    blocks, cur = [], []
    for line in lines:
        cur.append(line)
        if CITY_LINE.match(line):
            blocks.append(" ".join(cur))
            cur = []
    if cur:                                # trailing lines without a city line
        blocks.append(" ".join(cur))
    res = blocks[0] if blocks else ""
    po = blocks[1] if len(blocks) > 1 else ""
    extra = " | ".join(blocks[2:]) if len(blocks) > 2 else ""
    return res, po, extra


def parse_metadata(text):
    m = {}
    g = lambda pat, flags=re.I: (re.search(pat, text, flags) or [None, ""])[1].strip()
    m["filing_no"] = g(r"Filing Number:\s*(\d+)")
    m["filing_date"] = g(r"Filing Number:\s*\d+\s+Date:\s*([\d/]+)")
    m["state_id"] = g(r"Identification Number:\s*(\d{9})")
    m["entity_name"] = g(r"exact name of the corporation is:\s*\n?\s*(.+?)\s*\n")
    # Principal office (Article VII a)
    sec = re.search(r"principal office of the corporation in\s*Massachusetts is:(.*?)b\.\s*The name, residential",
                    text, re.S | re.I)
    m["principal_office"] = _addr_from_fields(sec.group(1)) if sec else ""
    # Resident agent (Article VII d)
    sec = re.search(r"resident agent, if any, of the business entity is:(.*?)I/We, the below", text, re.S | re.I)
    if sec:
        blk = sec.group(1)
        m["agent_name"] = (re.search(r"Name:\s*(.+?)(?:No\. and Street|\n)", blk) or [None, ""])[1].strip()
        m["agent_address"] = _addr_from_fields(blk)
    else:
        m["agent_name"] = m["agent_address"] = ""
    return m


def _addr_from_fields(blk):
    st = (re.search(r"No\. and Street:\s*(.+)", blk) or [None, ""])[1].strip()
    city = (re.search(r"City or Town:\s*(.+?)\s+State:", blk) or [None, ""])[1].strip()
    state = (re.search(r"State:\s*([A-Z]{2})", blk) or [None, ""])[1].strip()
    zp = (re.search(r"Zip:\s*([\d-]+)", blk) or [None, ""])[1].strip()
    return f"{st} {city}, {state} {zp}".strip(" ,")


def find_officer_rows(pdf):
    """Yield (title, name, address_cell, term) from the Article VII(b) table(s)."""
    in_table = False
    for page in pdf.pages:
        for table in page.extract_tables():
            if not table:
                continue
            first = [(c or "").strip() for c in table[0]]
            is_header = (len(first) >= 3 and first[0].upper().startswith("TITLE")
                         and "NAME" in first[1].upper())
            rows = table[1:] if is_header else table
            if is_header:
                in_table = True
            elif not in_table:
                continue
            for r in rows:
                cells = [(c or "").strip() for c in r]
                if len(cells) < 3:
                    continue
                title = cells[0].upper().replace("\n", " ").strip()
                if not title or title.startswith("TITLE"):
                    continue
                if title not in REQUIRED_TITLES and not re.fullmatch(r"[A-Z .\-/]+", title):
                    # not a title cell -> we've left the officer table
                    in_table = False
                    break
                name = re.sub(r"\s+", " ", cells[1])
                term = cells[3] if len(cells) > 3 else ""
                yield title, name, cells[2], term


def parse_pdf(path):
    with pdfplumber.open(path) as pdf:
        # Every filing bookends the actual filed Articles of Organization with
        # a generic typed certificate page from the state ("I hereby certify
        # that, upon examination...") - and this shows up as BOTH the first
        # and last page, not just the first. When the real content (page 2+)
        # is a scanned image with no text layer, the two certificate copies
        # alone can still clear a whole-document length check, so judge
        # needs_ocr on the non-certificate pages only.
        page_texts = [(p.extract_text() or "") for p in pdf.pages]
        substantive_pages = [t for t in page_texts if not _is_certificate_page(t)]
        substantive_text = "\n".join(substantive_pages) if substantive_pages else "\n".join(page_texts)
        if len(substantive_text.strip()) < 200:
            return None, [], "needs_ocr"
        text = "\n".join(page_texts)
        meta = parse_metadata(text)
        meta["file"] = str(path)
        people = []
        n_principal = norm_addr(meta["principal_office"])
        n_agent = norm_addr(meta["agent_address"])
        for title, name, cell, term in find_officer_rows(pdf):
            res, po, extra = split_address_cell(cell)
            n_res = norm_addr(res)
            people.append({
                "state_id": meta["state_id"],
                "filing_no": meta["filing_no"],
                "entity_name": meta["entity_name"],
                "title": title,
                "name": name,
                "residential_address": res,
                "po_address": po,
                "extra_address_lines": extra,
                "term_expires": term,
                "res_missing": int(not res),
                "res_pobox": int(bool(POBOX.search(res))),
                "res_co": int(bool(CARE_OF.search(res))),
                "res_eq_principal": int(bool(n_res) and n_res == n_principal),
                "res_eq_agent": int(bool(n_res) and bool(n_agent) and n_res == n_agent),
                "res_eq_po": int(bool(n_res) and n_res == norm_addr(po)),
                "res_unit": int(bool(UNIT.search(res))),
            })
        if not people:
            return meta, [], "no_table"
        return meta, people, "ok"


def roll_up(meta, people):
    titles = {p["title"] for p in people}
    return {
        "state_id": meta["state_id"],
        "filing_no": meta["filing_no"],
        "filing_date": meta["filing_date"],
        "entity_name": meta["entity_name"],
        "principal_office": meta["principal_office"],
        "agent_name": meta["agent_name"],
        "agent_address": meta["agent_address"],
        "n_persons": len(people),
        "has_president": int("PRESIDENT" in titles),
        "has_treasurer": int("TREASURER" in titles),
        "has_clerk": int("CLERK" in titles),
        "n_directors": sum(p["title"] == "DIRECTOR" for p in people),
        "n_res_missing": sum(p["res_missing"] for p in people),
        "n_res_pobox": sum(p["res_pobox"] for p in people),
        "n_res_co": sum(p["res_co"] for p in people),
        "n_res_eq_principal": sum(p["res_eq_principal"] for p in people),
        "n_res_eq_agent": sum(p["res_eq_agent"] for p in people),
        "all_res_present": int(all(not p["res_missing"] and not p["res_pobox"] for p in people)),
        "file": meta["file"],
    }


def main(target, entities_csv=None):
    target = Path(target)
    files = sorted(target.glob("*.pdf")) if target.is_dir() else [target]

    names_by_id = {}
    if entities_csv:
        import csv as _csv
        with open(entities_csv, newline="", encoding="utf-8-sig") as fh:
            for row in _csv.DictReader(fh):
                names_by_id[(row.get("ID Number") or "").strip()] = row.get("Entityname") or ""

    officers, entities, log = [], [], []
    for f in files:
        try:
            meta, people, status = parse_pdf(f)
        except Exception as e:
            meta, people, status = None, [], f"error: {e!r}"[:120]
        log.append({"file": str(f), "status": status,
                    "state_id": meta["state_id"] if meta else f.stem,
                    "entity_name": (meta["entity_name"] if meta and meta.get("entity_name") else names_by_id.get(f.stem, "")),
                    "n_persons": len(people)})
        officers.extend(people)
        if meta and people:
            entities.append(roll_up(meta, people))
        print(f"[{status}] {f.name}  persons={len(people)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in (("parsed_officers.csv", officers),
                       ("parsed_entities.csv", entities),
                       ("parse_log.csv", log)):
        if rows:
            with open(OUTPUT_DIR / name, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
    print(f"\n{len(files)} files, {len(entities)} parsed, {len(officers)} persons")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
