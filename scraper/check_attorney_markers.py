"""
Scan the PDFs that didn't parse into structured officer rows for signals
that the filing was attorney/professional-prepared rather than filed
directly through the state's own online form: a DocuSign envelope ID, a
fax transmission header, "Continuation Sheet" references, and common
attorney/law-firm text (Esq., Law Office, LLP, P.C., etc.) - the same
markers found for real on the POLYBIO filing during development.

These are signals to weigh, not proof - a DIY filer could use DocuSign
too, and an attorney could type directly into the state's own form.

Re-extracts text (including OCR where needed) for each PDF - this is the
slow part, roughly as slow as the original parse_articles.py OCR pass,
since these are exactly the PDFs that needed OCR the first time.

Usage:
    python scraper/check_attorney_markers.py
    python scraper/check_attorney_markers.py --statuses no_table_ocr no_table needs_ocr

Reads:
    data/output/parse_log.csv
Writes:
    data/output/attorney_markers.csv
"""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_articles import get_page_texts, ocr_scanned_pages, _is_certificate_page  # noqa: E402

OUTPUT_DIR = Path("data/output")

MARKERS = {
    "docusign": re.compile(r"DOCUSIGN ENVELOPE ID", re.I),
    # Two phone-number-length digit runs close together on one line, e.g.
    # "12/28/2022 10:35 AM 17814606994 '> 16176243891 pg 3 of 7" - the
    # arrow glyph between them OCRs inconsistently, so match on the
    # digit-run proximity instead of a specific separator character.
    "fax_header": re.compile(r"\b\d{10,11}\b[^\d\n]{1,15}\d{10,11}\b"),
    "continuation_sheet": re.compile(r"CONTINUATION SHEET", re.I),
    "attorney_text": re.compile(r"\b(ESQ\.?|ESQUIRE|LAW OFFICES?|LAW FIRM|ATTORNEYS? AT LAW|LLP|P\.?C\.?)\b", re.I),
}


def get_full_text(pdf_path):
    page_texts = get_page_texts(pdf_path)
    substantive_pages = [t for t in page_texts if not _is_certificate_page(t)]
    substantive_text = "\n".join(substantive_pages) if substantive_pages else "\n".join(page_texts)
    if len(substantive_text.strip()) < 200:
        page_texts = ocr_scanned_pages(pdf_path, page_texts)
    return "\n".join(page_texts)


def main(statuses):
    log_path = OUTPUT_DIR / "parse_log.csv"
    with open(log_path, newline="", encoding="utf-8") as f:
        targets = [row for row in csv.DictReader(f) if row["status"] in statuses]

    print(f"Scanning {len(targets)} PDFs (statuses: {', '.join(sorted(statuses))}) for attorney-filing markers...")

    out_rows = []
    for i, row in enumerate(targets, 1):
        pdf_path = Path(row["file"])
        result = {
            "state_id": row.get("state_id", ""),
            "entity_name": row.get("entity_name", ""),
            "pdf_path": str(pdf_path),
            "status": row["status"],
        }
        try:
            text = get_full_text(pdf_path)
        except Exception as e:
            result["error"] = str(e)[:150]
            for key in MARKERS:
                result[key] = ""
            result["any_marker"] = ""
            out_rows.append(result)
            print(f"[{i}/{len(targets)}] {pdf_path.name}: ERROR {result['error']}")
            continue

        result["error"] = ""
        hits = []
        for key, pattern in MARKERS.items():
            hit = bool(pattern.search(text))
            result[key] = int(hit)
            if hit:
                hits.append(key)
        result["any_marker"] = int(bool(hits))
        out_rows.append(result)
        print(f"[{i}/{len(targets)}] {pdf_path.name}: {', '.join(hits) if hits else 'none'}")

    if not out_rows:
        print("Nothing to scan.")
        return

    fieldnames = ["state_id", "entity_name", "pdf_path", "status",
                  "docusign", "fax_header", "continuation_sheet", "attorney_text",
                  "any_marker", "error"]
    out_path = OUTPUT_DIR / "attorney_markers.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    scanned = [r for r in out_rows if r["error"] == ""]
    n, ns = len(out_rows), len(scanned)
    print()
    print(f"Wrote {n} rows ({ns} successfully scanned, {n - ns} errors) to {out_path}")
    for key in MARKERS:
        hits = sum(r[key] for r in scanned)
        print(f"  {key:20s} {hits}/{ns} ({100 * hits / ns:.1f}%)" if ns else f"  {key:20s} 0")
    any_hits = sum(r["any_marker"] for r in scanned)
    print(f"  {'any marker':20s} {any_hits}/{ns} ({100 * any_hits / ns:.1f}%)" if ns else "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--statuses", nargs="+", default=["no_table_ocr"],
                     help="parse_log.csv statuses to scan (default: no_table_ocr)")
    args = ap.parse_args()
    main(set(args.statuses))
