#!/usr/bin/env python3
"""
extract_proof.py — Extract cropped, highlighted screenshots from PDF documents.

Uses PyMuPDF's text search to find exact coordinates, then renders a cropped
section of the page with a translucent green highlight over the found text.

Modes:
  find    — Locate where text appears in a PDF (page numbers + coordinates)
  extract — Crop a screenshot with a highlight drawn at the exact match
  verify  — Extract + read back text from the highlighted region to confirm
             the match is correct. Returns structured pass/fail with confidence.

Usage:
  # Find where text appears
  python3 extract_proof.py --pdf doc.pdf --search "1,250.00" --mode find

  # Extract a cropped screenshot with highlight
  python3 extract_proof.py --pdf doc.pdf --search "1,250.00" --page 3 \
    --output proof.png --highlight value

  # Verify: extract + cross-check the highlighted text
  python3 extract_proof.py --pdf doc.pdf --search "1,250.00" --page 3 \
    --output proof.png --mode verify --json
"""

import argparse
import json
import sys
import os
import re
import unicodedata


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------

_LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

_INVISIBLE_CHARS = "\u00ad\u200b\u200c\u200d\u2060\ufeff"


def _normalize_text_for_search(text):
    """Normalize text for searching: Unicode NFC, ligature decomposition,
    and invisible character removal."""
    text = unicodedata.normalize("NFC", text)
    for lig, replacement in _LIGATURE_MAP.items():
        text = text.replace(lig, replacement)
    for ch in _INVISIBLE_CHARS:
        text = text.replace(ch, "")
    return text


def ensure_dependencies():
    """Install pymupdf and pillow if not available."""
    try:
        import fitz
    except ImportError:
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "pymupdf",
            "--break-system-packages", "-q"
        ])
    try:
        from PIL import Image
    except ImportError:
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "Pillow",
            "--break-system-packages", "-q"
        ])


ensure_dependencies()

import fitz
from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------

def _has_text_layer(page):
    """Check whether a page has an embedded text layer (not scanned-only)."""
    text = page.get_text("text").strip()
    return len(text) > 20  # Arbitrary threshold — a real text layer has content


def _ocr_search(page, search_text):
    """Use Tesseract OCR via PyMuPDF to search a scanned/image-only page.

    Returns a list of fitz.Rect objects, same as page.search_for().
    Requires tesseract to be installed on the system.
    """
    try:
        tp = page.get_textpage_ocr(
            language="eng",
            dpi=300,
            full=True,
        )
        # search_for with a pre-built textpage
        areas = page.search_for(search_text, textpage=tp)
        return areas, True
    except Exception as e:
        print(f"  OCR failed: {e}", file=sys.stderr)
        print("  Install tesseract for scanned PDF support: "
              "apt-get install tesseract-ocr", file=sys.stderr)
        return [], False


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def find_text(pdf_path, search_text, page_num=None, ocr=False):
    """
    Search for text in a PDF. Returns list of dicts with page number and rect.
    If page_num is given (1-indexed), only searches that page.
    If ocr=True, falls back to Tesseract OCR for pages without a text layer.
    """
    doc = fitz.open(pdf_path)
    results = []
    ocr_used = False

    pages_to_search = range(len(doc))
    if page_num is not None:
        pages_to_search = [page_num - 1]

    for p_idx in pages_to_search:
        if p_idx < 0 or p_idx >= len(doc):
            continue
        page = doc[p_idx]

        # Try normal text search first
        areas = page.search_for(search_text)

        # If no results, try text-normalized form (Unicode NFC, ligatures, etc.)
        if not areas:
            normalized = _normalize_text_for_search(search_text)
            if normalized != search_text:
                areas = page.search_for(normalized)

        # If no results and OCR is enabled, try OCR on pages without text
        if not areas and ocr and not _has_text_layer(page):
            print(f"  Page {p_idx + 1}: no text layer detected, running OCR...",
                  file=sys.stderr)
            areas, ocr_ok = _ocr_search(page, search_text)
            if ocr_ok:
                ocr_used = True

        for area in areas:
            results.append({
                "page": p_idx + 1,
                "page_idx": p_idx,
                "rect": area,
                "x0": round(area.x0, 2),
                "y0": round(area.y0, 2),
                "x1": round(area.x1, 2),
                "y1": round(area.y1, 2),
                "ocr": ocr_used,
            })

    doc.close()
    return results


def _select_match(areas, match_index, prefer, search_text, page_num):
    """Pick the best match from a list of fitz.Rect results.

    Returns (selected_rect, index, confidence, note).
    Confidence: "high" if single match, "medium" if auto-selected from
    multiple, "low" if a fallback variation was used.
    """
    if match_index is not None and 0 <= match_index < len(areas):
        return areas[match_index], match_index, "high", "Explicit match index"

    if len(areas) == 1:
        return areas[0], 0, "high", "Single match on page"

    # Multiple matches — print warning and auto-select
    print(f"  WARNING: {len(areas)} matches for '{search_text}' on page {page_num}:",
          file=sys.stderr)
    for i, a in enumerate(areas):
        rightmost = a.x0 == max(r.x0 for r in areas)
        print(f"    [{i}] x={a.x0:.0f}, y={a.y0:.0f}"
              f"{'  (rightmost)' if rightmost else ''}",
              file=sys.stderr)

    if prefer == "right":
        selected = max(areas, key=lambda r: r.x0)
    elif prefer == "last":
        selected = areas[-1]
    else:
        selected = areas[0]

    sel_idx = areas.index(selected)
    print(f"  -> Using match [{sel_idx}] (--prefer {prefer}). "
          f"Override with --match-index N if wrong.", file=sys.stderr)
    return selected, sel_idx, "medium", f"Auto-selected ({prefer}) from {len(areas)} matches"


def _find_text_in_page(page, search_text):
    """Extract full page text and find the search string with flexible matching.

    Strips invisible characters and normalizes Unicode on both sides, then
    returns the *actual text as it appears in the PDF* so it can be fed back
    to page.search_for() for precise coordinates.  Returns None if not found.
    """
    page_text = page.get_text("text")
    if not page_text:
        return None

    norm_page = unicodedata.normalize("NFC", page_text)
    norm_search = _normalize_text_for_search(search_text)

    # Fast path: case-insensitive find on NFC-normalized text
    idx = norm_page.lower().find(norm_search.lower())
    if idx >= 0:
        return norm_page[idx:idx + len(norm_search)]

    # Slow path: strip invisible chars, build index map back to original
    invisible = set(_INVISIBLE_CHARS)
    clean_map = []  # clean_idx -> original_idx
    clean_chars = []
    for orig_idx, ch in enumerate(norm_page):
        if ch not in invisible:
            clean_map.append(orig_idx)
            clean_chars.append(ch)
    clean_text = "".join(clean_chars)

    idx = clean_text.lower().find(norm_search.lower())
    if idx >= 0 and idx + len(norm_search) <= len(clean_map):
        orig_start = clean_map[idx]
        orig_end = clean_map[idx + len(norm_search) - 1] + 1
        return norm_page[orig_start:orig_end]

    return None


def _try_variations(page, search_text, ocr=False):
    """Try common formatting variations of the search text.

    Returns (areas, variation_used, is_fallback, used_ocr).
    """
    used_ocr = False

    def _search(text):
        return page.search_for(text)

    # Phase 0: Exact match
    areas = _search(search_text)
    if areas:
        return areas, search_text, False, False

    # Phase 1: Text-normalized search (Unicode NFC, ligatures, invisible chars)
    normalized = _normalize_text_for_search(search_text)
    if normalized != search_text:
        areas = _search(normalized)
        if areas:
            return areas, normalized, True, False

    # Phase 2: Page-text-extraction fallback — find actual PDF string flexibly
    found_actual = _find_text_in_page(page, search_text)
    if found_actual and found_actual != search_text and found_actual != normalized:
        areas = _search(found_actual)
        if areas:
            return areas, found_actual, True, False

    # Phase 3: Numeric/financial formatting variations
    variations = [
        search_text.replace(",", ""),
        f"({search_text})",
        f"({search_text}.)",
        search_text + ".",
        "-" + search_text,
        search_text.lstrip("-"),
        search_text.replace("$", ""),
        search_text.replace("$", "").replace(",", ""),
    ]
    seen = {search_text, normalized}
    if found_actual:
        seen.add(found_actual)
    for v in variations:
        if v not in seen:
            seen.add(v)
            areas = _search(v)
            if areas:
                return areas, v, True, False

    # Phase 4: OCR fallback (for scanned/image-only pages)
    if ocr and not _has_text_layer(page):
        print(f"  No text layer — running OCR...", file=sys.stderr)
        areas, ocr_ok = _ocr_search(page, search_text)
        if areas:
            return areas, search_text, False, True
        if ocr_ok:
            for v in variations:
                if v in seen:
                    areas, _ = _ocr_search(page, v)
                    if areas:
                        return areas, v, True, True

    return [], None, False, False


def _read_text_at_rect(page, rect, expand=2):
    """Extract the actual text content at a given rectangle.

    Expands the rect slightly to ensure full character capture.
    Returns the extracted text string.
    """
    expanded = fitz.Rect(
        rect.x0 - expand,
        rect.y0 - expand,
        rect.x1 + expand,
        rect.y1 + expand,
    )
    words = page.get_text("words")
    matched = []
    for w in words:
        wr = fitz.Rect(w[:4])
        if wr.intersects(expanded):
            matched.append(w[4])
    return " ".join(matched)


def _normalize_for_comparison(text):
    """Normalize text for fuzzy comparison (Unicode, ligatures, invisible chars,
    whitespace, punctuation, case)."""
    text = _normalize_text_for_search(text)
    return re.sub(r"[\s,$().%-]+", "", text).lower()


def extract_crop(pdf_path, search_text, page_num, output_path,
                 highlight="value", context=80, scale=5, pad=8,
                 margin_left=30, margin_right=10, match_index=None,
                 prefer="right", verify=False, ocr=False):
    """
    Extract a cropped region from a PDF page centered on the found text,
    with a precise highlight drawn at the exact text coordinates.

    search_text can be a single string or a list of strings. When a list is
    given, each term gets its own highlight and the crop spans all of them.

    Returns:
        dict with result info including confidence and optional verification
    """
    # Normalise to list so the rest of the code is uniform
    search_terms = [search_text] if isinstance(search_text, str) else list(search_text)

    doc = fitz.open(pdf_path)
    page_idx = page_num - 1

    if page_idx < 0 or page_idx >= len(doc):
        doc.close()
        return {"error": f"Page {page_num} out of range (PDF has {len(doc)} pages)"}

    page = doc[page_idx]
    page_rect = page.rect

    # --- locate every search term and pick the best match for each ----------
    matched_rects = []   # (rect, term, sel_idx, confidence, note, variation)
    errors = []

    for term in search_terms:
        areas, variation_used, is_fallback, used_ocr = _try_variations(
            page, term, ocr=ocr
        )
        if not areas:
            errors.append(term)
            continue

        text_rect, sel_idx, confidence, confidence_note = _select_match(
            areas, match_index, prefer, term, page_num
        )
        if is_fallback and confidence == "high":
            confidence = "medium"
            confidence_note += f" (matched variation '{variation_used}')"
        if used_ocr and confidence == "high":
            confidence = "medium"
            confidence_note += " (via OCR)"

        matched_rects.append({
            "rect": text_rect,
            "term": term,
            "sel_idx": sel_idx,
            "total_matches": len(areas),
            "confidence": confidence,
            "confidence_note": confidence_note,
            "variation": variation_used if is_fallback else None,
            "ocr": used_ocr,
        })

    if not matched_rects:
        doc.close()
        return {"error": f"Text not found on page {page_num}: {errors}"}

    # --- compute crop region spanning all matched rects ---------------------
    all_y0 = min(m["rect"].y0 for m in matched_rects)
    all_y1 = max(m["rect"].y1 for m in matched_rects)

    crop_x0 = margin_left
    crop_y0 = max(0, all_y0 - context)
    crop_x1 = page_rect.width - margin_right
    crop_y1 = min(page_rect.height, all_y1 + context)

    crop = fitz.Rect(crop_x0, crop_y0, crop_x1, crop_y1)

    # --- render -------------------------------------------------------------
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=crop)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # --- draw highlights for every matched rect ----------------------------
    if highlight != "none":
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        for m in matched_rects:
            r = m["rect"]
            if highlight == "value":
                hx0 = (r.x0 - pad - crop_x0) * scale
                hy0 = (r.y0 - pad - crop_y0) * scale
                hx1 = (r.x1 + pad - crop_x0) * scale
                hy1 = (r.y1 + pad - crop_y0) * scale
            elif highlight == "row":
                hx0 = (crop_x0 + 5 - crop_x0) * scale
                hy0 = (r.y0 - pad - crop_y0) * scale
                hx1 = (crop_x1 - 5 - crop_x0) * scale
                hy1 = (r.y1 + pad - crop_y0) * scale

            overlay_draw.rectangle(
                [hx0, hy0, hx1, hy1],
                fill=(249, 115, 22, 80),  # soft orange, ~31% opacity
            )

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # --- save ---------------------------------------------------------------
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)

    # --- build result -------------------------------------------------------
    primary = matched_rects[0]
    result = {
        "output": output_path,
        "page": page_num,
        "search_text": search_terms[0] if len(search_terms) == 1 else search_terms,
        "found_at": {
            "x0": round(primary["rect"].x0, 2),
            "y0": round(primary["rect"].y0, 2),
            "x1": round(primary["rect"].x1, 2),
            "y1": round(primary["rect"].y1, 2),
        },
        "highlights": len(matched_rects),
        "confidence": min((m["confidence"] for m in matched_rects),
                          key=lambda c: ["high", "medium", "low"].index(c)),
        "image_size": {"width": img.width, "height": img.height},
    }

    if errors:
        result["not_found"] = errors

    if len(matched_rects) > 1:
        result["matched_terms"] = [
            {"term": m["term"],
             "confidence": m["confidence"],
             "confidence_note": m["confidence_note"]}
            for m in matched_rects
        ]

    # --- verification (for each term) --------------------------------------
    if verify:
        verifications = []
        all_pass = True
        for m in matched_rects:
            readback = _read_text_at_rect(page, m["rect"])
            norm_search = _normalize_for_comparison(m["term"])
            norm_readback = _normalize_for_comparison(readback)
            text_match = norm_search in norm_readback or norm_readback in norm_search
            if not text_match:
                all_pass = False
                print(f"  VERIFY FAIL: searched '{m['term']}', "
                      f"readback '{readback}'", file=sys.stderr)
            verifications.append({
                "search_text": m["term"],
                "readback_text": readback,
                "text_match": text_match,
                "status": "pass" if text_match else "fail",
            })

        result["verification"] = verifications if len(verifications) > 1 else verifications[0]
        if not all_pass:
            result["confidence"] = "low"

    doc.close()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract cropped, highlighted screenshots from PDFs"
    )
    parser.add_argument("--pdf", required=True, help="Path to the PDF file")
    parser.add_argument("--search", required=True, nargs="+",
                        help="Text to search for (multiple terms = multiple "
                             "highlights on the same screenshot)")
    parser.add_argument("--mode", default="extract",
                        choices=["find", "extract", "verify"],
                        help="'find' to locate text, 'extract' to crop+highlight, "
                             "'verify' to extract + cross-check")
    parser.add_argument("--page", type=int, default=None,
                        help="1-indexed page number (required for extract/verify)")
    parser.add_argument("--output", default="proof.png",
                        help="Output PNG path (extract/verify mode)")
    parser.add_argument("--highlight", default="value",
                        choices=["value", "row", "none"],
                        help="Highlight style")
    parser.add_argument("--context", type=int, default=80,
                        help="Vertical context in PDF points (default: 80)")
    parser.add_argument("--scale", type=int, default=5,
                        help="Render scale factor (default: 5)")
    parser.add_argument("--prefer", default="right",
                        choices=["right", "first", "last"],
                        help="When multiple matches: 'right' (rightmost, best for "
                             "forms), 'first', or 'last' (default: right)")
    parser.add_argument("--match-index", type=int, default=None,
                        help="Use the Nth match (0-indexed), overrides --prefer")
    parser.add_argument("--ocr", action="store_true",
                        help="Enable OCR fallback for scanned/image-only PDFs "
                             "(requires tesseract)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    # Normalise: single search term → string, multiple → list
    search = args.search[0] if len(args.search) == 1 else args.search

    if args.mode == "find":
        # Find mode: search for each term
        terms = args.search
        all_results = []
        for term in terms:
            results = find_text(args.pdf, term, args.page, ocr=args.ocr)
            for r in results:
                r["search_text"] = term
            all_results.extend(results)
        if args.json:
            for r in all_results:
                r["rect"] = str(r["rect"])
            print(json.dumps(all_results, indent=2))
        else:
            if not all_results:
                print(f"Not found: {terms}")
            else:
                for r in all_results:
                    print(f"  '{r.get('search_text', '')}' → "
                          f"Page {r['page']}: ({r['x0']}, {r['y0']}) "
                          f"to ({r['x1']}, {r['y1']})")

    elif args.mode in ("extract", "verify"):
        if args.page is None:
            # Auto-find page from first search term
            first_term = args.search[0]
            results = find_text(args.pdf, first_term, ocr=args.ocr)
            if not results:
                print(f"Error: '{first_term}' not found in any page",
                      file=sys.stderr)
                sys.exit(1)
            args.page = results[0]["page"]
            if not args.json:
                print(f"Auto-found on page {args.page}")

        result = extract_crop(
            args.pdf, search, args.page, args.output,
            highlight=args.highlight,
            context=args.context,
            scale=args.scale,
            match_index=args.match_index,
            prefer=args.prefer,
            verify=(args.mode == "verify"),
            ocr=args.ocr,
        )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(f"Error: {result['error']}", file=sys.stderr)
                sys.exit(1)
            else:
                conf = result["confidence"]
                conf_icon = {"high": "+", "medium": "~", "low": "!"}[conf]
                n = result.get("highlights", 1)
                print(f"Saved: {result['output']} "
                      f"({result['image_size']['width']}x"
                      f"{result['image_size']['height']}, "
                      f"{n} highlight{'s' if n > 1 else ''})")
                print(f"  Confidence: [{conf_icon}] {conf}")


if __name__ == "__main__":
    main()
