"""
Extract incorporator / resident-agent addresses as originally filed, from
downloaded Articles of Organization PDFs.

For each PDF in --pdf-dir:
  1. Try direct text extraction (pdfplumber). Most 2021+ MA filings are
     e-filed and have a real text layer.
  2. If that yields too little text (likely a scanned image), fall back to
     OCR (pdf2image + pytesseract). Requires poppler and tesseract installed
     on the system (see README).
  3. Save the raw extracted text to data/text/<id>.txt - always, even when
     parsing below succeeds - so field-matching can be refined without
     re-running OCR.
  4. Look for labeled blocks ("incorporator", "resident agent") and pull the
     address-looking lines that follow. When no label matches, the row is
     marked for manual review with the raw first-page text attached instead
     of guessing.

The label patterns are a best-effort starting point, not verified against a
real filed form in this environment. Run this on 3-5 downloaded PDFs first,
check data/output/addresses.csv "confidence" column, and inspect the
matching data/text/<id>.txt for any row marked manual_review to refine the
LABEL_PATTERNS regexes below.

Usage:
    python scraper/extract_addresses.py --pdf-dir data/pdfs \
        --input entities.csv --output data/output/addresses.csv
"""
import argparse
import csv
import re
from pathlib import Path

import pdfplumber

MIN_TEXT_LEN = 40  # below this, assume the PDF is a scanned image and OCR it

ZIP_RE = r"\d{5}(?:-\d{4})?"
ADDRESS_LINE_RE = re.compile(
    rf"\d+[^,\n]{{2,60}},\s*[A-Za-z .'-]+,?\s*[A-Z]{{2}}\s*{ZIP_RE}", re.I
)

LABEL_PATTERNS = {
    "incorporator": re.compile(r"(?i)name\s*(?:and|&)?\s*address\s*of\s*(?:each\s*)?incorporator"),
    "resident_agent": re.compile(r"(?i)resident\s*agent"),
}

FIELDS = [
    "id_number", "entity_name", "text_source",
    "incorporator_block", "incorporator_address",
    "resident_agent_block", "resident_agent_address",
    "confidence", "note",
]


def extract_text_pdfplumber(pdf_path: Path) -> str:
    text_parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_parts.append(t)
    return "\n".join(text_parts)


def extract_text_ocr(pdf_path: Path) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    images = convert_from_path(str(pdf_path), dpi=300)
    return "\n".join(pytesseract.image_to_string(img) for img in images)


def get_text(pdf_path: Path):
    text = extract_text_pdfplumber(pdf_path)
    if len(text.strip()) >= MIN_TEXT_LEN:
        return text, "text_layer"
    ocr_text = extract_text_ocr(pdf_path)
    return ocr_text, "ocr"


def find_block_after_label(lines, label_re, window=6):
    for i, line in enumerate(lines):
        if label_re.search(line):
            return "\n".join(lines[i: i + window]).strip()
    return ""


def find_address_in_block(block: str):
    m = ADDRESS_LINE_RE.search(block.replace("\n", ", "))
    return m.group(0) if m else ""


def process_pdf(pdf_path: Path, id_number: str, entity_name: str, text_dir: Path):
    try:
        text, source = get_text(pdf_path)
    except Exception as e:
        return dict(id_number=id_number, entity_name=entity_name, text_source="error",
                     incorporator_block="", incorporator_address="",
                     resident_agent_block="", resident_agent_address="",
                     confidence="error", note=str(e))

    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / f"{id_number}.txt").write_text(text, encoding="utf-8")

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    incorporator_block = find_block_after_label(lines, LABEL_PATTERNS["incorporator"])
    agent_block = find_block_after_label(lines, LABEL_PATTERNS["resident_agent"])

    incorporator_addr = find_address_in_block(incorporator_block)
    agent_addr = find_address_in_block(agent_block)

    if incorporator_addr or agent_addr:
        confidence = "labeled_match"
        note = ""
    else:
        confidence = "manual_review"
        note = "no labeled block matched - see text file / refine LABEL_PATTERNS"

    return dict(
        id_number=id_number, entity_name=entity_name, text_source=source,
        incorporator_block=incorporator_block, incorporator_address=incorporator_addr,
        resident_agent_block=agent_block, resident_agent_address=agent_addr,
        confidence=confidence, note=note,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", default="data/pdfs")
    ap.add_argument("--text-dir", default="data/text")
    ap.add_argument("--input", required=True, help="original entities CSV, for entity names")
    ap.add_argument("--output", default="data/output/addresses.csv")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    import pandas as pd
    df = pd.read_csv(args.input, dtype=str).fillna("")
    names_by_id = dict(zip(df["ID Number"].astype(str).str.strip(), df["Entityname"]))

    pdf_dir = Path(args.pdf_dir)
    text_dir = Path(args.text_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for pdf_path in pdfs:
            id_number = pdf_path.stem
            entity_name = names_by_id.get(id_number, "")
            print(f"parsing {id_number} {entity_name}")
            result = process_pdf(pdf_path, id_number, entity_name, text_dir)
            w.writerow(result)


if __name__ == "__main__":
    main()
