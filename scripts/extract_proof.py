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
                 highlight="value", context=80, scale=3, pad=8,
                 margin_left=30, margin_right=10, match_index=None,
                 prefer="right", verify=False, ocr=False):
    """
    Extract a cropped region from a PDF page centered on the found text,
    with a precise highlight drawn at the exact text coordinates.

    Args:
        pdf_path: Path to the PDF
        search_text: Text to find and highlight
        page_num: 1-indexed page number
        output_path: Where to save the PNG
        highlight: "value" (tight box), "row" (full-width band), or "none"
        context: PDF points of vertical context above/below the text
        scale: Render scale factor (3 = high-res)
        pad: Padding around highlight box in PDF points
        margin_left: Left margin in PDF points
        margin_right: Right margin from page edge in PDF points
        match_index: If set, use the Nth match (0-indexed). Otherwise auto-select.
        prefer: "right" (best for forms), "first", or "last"
        verify: If True, read back text from the highlighted region and
                cross-check against search_text
        ocr: If True, fall back to Tesseract OCR for scanned/image-only pages

    Returns:
        dict with result info including confidence and optional verification
    """
    doc = fitz.open(pdf_path)
    page_idx = page_num - 1

    if page_idx < 0 or page_idx >= len(doc):
        doc.close()
        return {"error": f"Page {page_num} out of range (PDF has {len(doc)} pages)"}

    page = doc[page_idx]
    areas, variation_used, is_fallback, used_ocr = _try_variations(
        page, search_text, ocr=ocr
    )

    if not areas:
        doc.close()
        return {"error": f"Text '{search_text}' not found on page {page_num}"}

    text_rect, sel_idx, confidence, confidence_note = _select_match(
        areas, match_index, prefer, search_text, page_num
    )

    # Downgrade confidence if we had to use a variation
    if is_fallback and confidence == "high":
        confidence = "medium"
        confidence_note += f" (matched variation '{variation_used}')"

    page_rect = page.rect

    # Calculate crop region
    crop_x0 = margin_left
    crop_y0 = max(0, text_rect.y0 - context)
    crop_x1 = page_rect.width - margin_right
    crop_y1 = min(page_rect.height, text_rect.y1 + context)

    crop = fitz.Rect(crop_x0, crop_y0, crop_x1, crop_y1)

    # Render the cropped region
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=crop)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # Draw highlight — semi-transparent fill (marker-pen style)
    if highlight != "none":
        if highlight == "value":
            hx0 = (text_rect.x0 - pad - crop_x0) * scale
            hy0 = (text_rect.y0 - pad - crop_y0) * scale
            hx1 = (text_rect.x1 + pad - crop_x0) * scale
            hy1 = (text_rect.y1 + pad - crop_y0) * scale
        elif highlight == "row":
            hx0 = (crop_x0 + 5 - crop_x0) * scale
            hy0 = (text_rect.y0 - pad - crop_y0) * scale
            hx1 = (crop_x1 - 5 - crop_x0) * scale
            hy1 = (text_rect.y1 + pad - crop_y0) * scale

        # Composite a translucent green highlight over the region
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [hx0, hy0, hx1, hy1],
            fill=(74, 222, 128, 80),  # soft green, ~31% opacity
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # Save
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)

    result = {
        "output": output_path,
        "page": page_num,
        "search_text": search_text,
        "found_at": {
            "x0": round(text_rect.x0, 2),
            "y0": round(text_rect.y0, 2),
            "x1": round(text_rect.x1, 2),
            "y1": round(text_rect.y1, 2),
        },
        "match_index": sel_idx,
        "total_matches": len(areas),
        "confidence": confidence,
        "confidence_note": confidence_note,
        "image_size": {"width": img.width, "height": img.height},
    }

    if is_fallback:
        result["matched_variation"] = variation_used
    if used_ocr:
        result["ocr"] = True
        if confidence == "high":
            result["confidence"] = "medium"
            result["confidence_note"] += " (via OCR)"

    # Verification: read text back from the highlighted region
    if verify:
        readback = _read_text_at_rect(page, text_rect)
        norm_search = _normalize_for_comparison(search_text)
        norm_readback = _normalize_for_comparison(readback)
        text_match = norm_search in norm_readback or norm_readback in norm_search

        result["verification"] = {
            "readback_text": readback,
            "search_normalized": norm_search,
            "readback_normalized": norm_readback,
            "text_match": text_match,
            "status": "pass" if text_match else "fail",
        }

        if not text_match:
            result["confidence"] = "low"
            result["confidence_note"] += " | VERIFICATION FAILED: readback mismatch"
            print(f"  VERIFY FAIL: searched '{search_text}', "
                  f"readback '{readback}'", file=sys.stderr)

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
    parser.add_argument("--search", required=True, help="Text to search for")
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
    parser.add_argument("--scale", type=int, default=3,
                        help="Render scale factor (default: 3)")
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

    if args.mode == "find":
        results = find_text(args.pdf, args.search, args.page, ocr=args.ocr)
        if args.json:
            for r in results:
                r["rect"] = str(r["rect"])
            print(json.dumps(results, indent=2))
        else:
            if not results:
                print(f"Not found: '{args.search}'")
                if args.page:
                    print(f"  (searched page {args.page} only)")
                else:
                    print(f"  (searched all pages)")
            else:
                for r in results:
                    print(f"  Page {r['page']}: ({r['x0']}, {r['y0']}) "
                          f"to ({r['x1']}, {r['y1']})")

    elif args.mode in ("extract", "verify"):
        if args.page is None:
            results = find_text(args.pdf, args.search, ocr=args.ocr)
            if not results:
                print(f"Error: '{args.search}' not found in any page",
                      file=sys.stderr)
                sys.exit(1)
            args.page = results[0]["page"]
            if not args.json:
                print(f"Auto-found on page {args.page}")

        result = extract_crop(
            args.pdf, args.search, args.page, args.output,
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
                print(f"Saved: {result['output']} "
                      f"({result['image_size']['width']}x"
                      f"{result['image_size']['height']})")
                print(f"  Found '{result['search_text']}' at page "
                      f"{result['page']}, "
                      f"({result['found_at']['x0']}, "
                      f"{result['found_at']['y0']})")
                print(f"  Confidence: [{conf_icon}] {conf} — "
                      f"{result['confidence_note']}")
                if "verification" in result:
                    v = result["verification"]
                    status = "PASS" if v["status"] == "pass" else "FAIL"
                    print(f"  Verification: {status} "
                          f"(readback: '{v['readback_text']}')")


if __name__ == "__main__":
    main()
