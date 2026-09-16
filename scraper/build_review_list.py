"""
Build one consolidated manual-review list combining every entity that
still needs a human: entities that never got a PDF downloaded at all
(from results.csv - unresolved name search, id_mismatch, no_articles,
error) and PDFs that downloaded but didn't parse into officer rows (from
parse_log.csv - needs_ocr, no_table, no_table_ocr), with entity names and
context so it's one file to work through instead of several.

Adds blank `reviewed` / `reviewer_notes` columns to fill in as you go.

Usage:
    python scraper/build_review_list.py entities.csv

Reads:
    data/output/results.csv (if present)
    data/output/parse_log.csv (if present)
Writes:
    data/output/needs_review.csv
"""
import csv
import sys
from pathlib import Path

OUTPUT_DIR = Path("data/output")

FIELDS = ["state_id", "entity_name", "date_of_organization", "stage",
          "reason", "note", "pdf_path", "reviewed", "reviewer_notes"]


def load_entities(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    while rows and not any(cell.strip() for cell in rows[0]):
        rows.pop(0)
    if not rows:
        return {}
    header, data_rows = rows[0], rows[1:]
    by_id = {}
    for row in data_rows:
        d = dict(zip(header, row))
        idn = (d.get("ID Number") or "").strip()
        if idn:
            by_id[idn] = d
    return by_id


def main(entities_csv):
    entities = load_entities(entities_csv)
    out_rows = []

    results_path = OUTPUT_DIR / "results.csv"
    if results_path.exists():
        with open(results_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["status"] == "ok":
                    continue
                idn = row["expected_id"]
                entity = entities.get(idn, {})
                out_rows.append({
                    "state_id": idn,
                    "entity_name": row["search_name"] or entity.get("Entityname", ""),
                    "date_of_organization": entity.get("DateOfOrganization", ""),
                    "stage": "not_downloaded",
                    "reason": row["status"],
                    "note": row["note"],
                    "pdf_path": "",
                })

    log_path = OUTPUT_DIR / "parse_log.csv"
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["status"] in ("ok", "ok_ocr"):
                    continue
                idn = row.get("state_id", "")
                entity = entities.get(idn, {})
                out_rows.append({
                    "state_id": idn,
                    "entity_name": row.get("entity_name") or entity.get("Entityname", ""),
                    "date_of_organization": entity.get("DateOfOrganization", ""),
                    "stage": "not_parsed",
                    "reason": row["status"],
                    "note": "",
                    "pdf_path": row["file"],
                })

    if not out_rows:
        print("Nothing to review - results.csv and parse_log.csv are all-ok or missing.")
        return

    for row in out_rows:
        row["reviewed"] = ""
        row["reviewer_notes"] = ""

    out_path = OUTPUT_DIR / "needs_review.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
