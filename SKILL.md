---
name: pdf-proof
description: "Use this skill when the user needs visual proof that specific values exist in a PDF — not just to read a PDF, but to see exactly where a number, amount, clause, or field appears on the page with a highlighted screenshot. Trigger when someone is cross-referencing a PDF against something else (a form they're filling out, a claim, a conversation, another document) and needs confirmation with evidence. Common signals: entering data into TurboTax or a form and pulling source numbers from last year's return; asking an accountant to verify reported figures; checking contract terms before sending to legal; matching invoice line items to a PO. The core intent is 'show me the actual text in context' — not summarize, not extract all text, but produce a cropped screenshot with the value highlighted. Do NOT trigger for PDF operations without a specific value to locate: merging, splitting, summarizing, converting, or creating PDFs."
---

# PDF Proof Page Generator

## What This Skill Does

When a user needs to verify that specific values match their source PDF documents, this skill:

1. Searches the PDF(s) for the exact text of each value
2. Crops a tight, readable section around each match
3. Draws a translucent green highlight over the found value (using exact text coordinates, not guesswork)
4. Cross-checks by reading text back from the highlighted region to confirm correctness
5. Generates a clean HTML proof page with a summary table, confidence indicators, and per-value proof cards showing the actual screenshots

The output is a standalone HTML file that references cropped PNG screenshots — a visual audit trail the user can keep, share, or refer back to.

**Important:** Always generate the HTML proof page in your first response. Do not just show screenshots inline or summarize values in chat — the shareable proof document is the whole point. Find, extract, and assemble the proof page in one pass.

## How It Works

The key insight is using PyMuPDF's `page.search_for(text)` to get **exact PDF-coordinate rectangles** for each value, then using those coordinates to draw highlights at mathematically precise positions. This avoids the problem of CSS-percentage-based overlays that never quite land in the right spot.

### Step-by-Step Process

#### 1. Identify what needs to be proven

From the user's request, extract:
- **Values to verify**: The specific numbers, amounts, or text the user wants confirmed
- **Source PDFs**: Which PDF documents contain the source data
- **Context for each value**: What form/line/field the value comes from (e.g., "Form 1040, line 15", "Invoice #4521, total amount", "Section 3.2, indemnification cap")

#### 2. Search and locate values in the PDFs

Use the bundled `scripts/extract_proof.py` script. It handles the PyMuPDF text search, cropping, and highlight drawing.

**Step A — Find first.** PDFs often contain the same value in multiple places — a form field and its instructions, a header and a total row, or a cross-reference on another page. Jumping straight to extract risks highlighting the wrong occurrence and producing a misleading proof. Run a find first to see all matches:

```bash
python3 /path/to/skill/scripts/extract_proof.py \
  --pdf "/path/to/source.pdf" \
  --search "3,000" \
  --page 39 \
  --mode find
```

Review the output — if there are multiple matches, note which index corresponds to the actual value vs. mentions in labels, headers, or instructions.

**Step B — Extract with verification:**

```bash
python3 /path/to/skill/scripts/extract_proof.py \
  --pdf "/path/to/source.pdf" \
  --search "1,250.00" \
  --page 5 \
  --context 80 \
  --output "/path/to/output/proof_total.png" \
  --highlight value \
  --mode verify \
  --json
```

Parameters:
- `--pdf`: Path to the source PDF
- `--search`: The exact text to find and highlight
- `--page`: 1-indexed page number to search on (if known; omit to search all pages)
- `--context`: How many PDF points of vertical context to include above and below the found text (default: 80). Increase for more surrounding context.
- `--output`: Where to save the cropped PNG
- `--highlight`: Highlight mode — `value` (tight box around the text), `row` (full-width band at the text's vertical position), or `none`
- `--scale`: Render scale factor (default: 3 for high-res)
- `--mode`: `find` (locate text), `extract` (crop+highlight), or `verify` (extract + read text back to cross-check)
- `--prefer`: When multiple matches exist — `right` (rightmost, best for forms), `first`, or `last` (default: `right`)
- `--match-index`: Use the Nth match (0-indexed), overrides `--prefer`
- `--json`: Output structured JSON with confidence and verification results

If you don't know the exact page number, run a search pass first:

```bash
python3 /path/to/skill/scripts/extract_proof.py \
  --pdf "/path/to/source.pdf" \
  --search "1,250.00" \
  --mode find
```

This prints every page and coordinate where the text appears, so you can pick the right one.

#### 3. Interpret confidence and verification results

When using `--mode verify --json`, the output includes:

```json
{
  "confidence": "high",
  "confidence_note": "Single match on page",
  "verification": {
    "readback_text": "1,250.00",
    "text_match": true,
    "status": "pass"
  }
}
```

**Confidence levels:**
- **high**: Single match on page, exact text found, verification passed
- **medium**: Auto-selected from multiple matches, or matched via a formatting variation (e.g., searched "3,000" but matched "3,000.")
- **low**: Verification failed (readback text doesn't match search text) — flag this to the user

**If verification fails**, the highlighted region may contain the wrong text. Re-run with `--mode find` to inspect all matches and use `--match-index` to select the correct one.

#### 4. Generate the HTML proof page

**The HTML proof page is the primary deliverable of this skill — always generate it.** Do not stop at showing inline screenshots or summarizing values in chat. The whole point is a standalone, shareable proof document. Generate the HTML proof page in your first response, not as an afterthought.

After creating all the cropped screenshots, generate an HTML file. Read the template from `assets/proof_template.html` and populate it. The template expects a JSON data structure — see the template comments for the schema.

Include confidence indicators in the proof page:
- High confidence values get a green checkmark
- Medium confidence values get a yellow warning
- Low confidence values get a red alert prompting manual review

Alternatively, construct the HTML directly following this structure:

```html
<!-- Summary table at top with all values -->
<div class="summary-box">
  <table>
    <tr><th>Field</th><th>Value</th><th>Source</th><th>Confidence</th></tr>
    <!-- One row per value being verified -->
  </table>
</div>

<!-- One proof card per value -->
<div class="proof-card">
  <div class="proof-header">
    <h3>Proof N: [Field Name]</h3>
    <span class="badge">[Value]</span>
    <span class="confidence-badge confidence-high">Verified</span>
  </div>
  <div class="proof-body">
    <p>[Brief explanation of where this value comes from]</p>
    <div class="screenshot">
      <img src="[relative path to cropped PNG]">
      <div class="screenshot-caption">[Source description]</div>
    </div>
  </div>
</div>
```

#### 5. Save outputs

Always create a dedicated subfolder for proof outputs — never drop PNGs into the user's main workspace. This keeps the workspace clean and groups related proof files together.

- Create a subfolder named after what's being verified, e.g., `proof-capital-loss-carryover/`, `proof-invoice-4521/`, `proof-contract-terms/`
- Save the HTML file and all PNG screenshots inside this subfolder
- Use relative paths in the HTML `<img src="">` tags (just the filename, no path prefix) so the page works when opened locally
- Share the link to the HTML file inside the subfolder

```
workspace/
  proof-capital-loss-carryover/
    proof.html
    proof_1040_line15.png
    proof_schedD_line7.png
    proof_schedD_line15.png
    proof_schedD_line21.png
```

## Important Technical Notes

### PyMuPDF installation
```bash
pip install pymupdf --break-system-packages -q
```

### Text search precision
`page.search_for()` returns `fitz.Rect` objects with exact `(x0, y0, x1, y1)` coordinates in PDF points. These are the source of truth for highlight positioning. CSS-percentage overlays and manual pixel guesses produce highlights that land in the wrong spot because PDF coordinate systems don't map cleanly to rendered pixel grids — this was the original failure mode that led to building the script.

### Multiple matches on the same page
A value like "3,000" might appear multiple times on a page — once as the actual form field value, and again in nearby instruction text or labels. When the script finds multiple matches, it prints a warning showing all matches with their coordinates and index numbers.

Run `--mode find` first whenever you're unsure a value is unique on the page. This avoids the most common failure: highlighting a mention of the value in nearby instruction text (e.g., "enter the amount from line 21, up to $3,000") instead of the actual form field. Review the match coordinates, identify which one is the real value, and use `--match-index N` to select it explicitly.

The script defaults to `--prefer right` which picks the rightmost match. This heuristic exists because structured forms (tax returns, invoices, applications) place values in right-hand columns while labels and instructions sit on the left. For non-form documents like contracts or reports where values appear inline in paragraphs, switch to `--prefer first` or pick manually with `--match-index`.

### Use verify mode for final extractions
The whole point of a proof page is trust — the user needs to know the highlighted value is actually the right one. `--mode verify` reads the text back from the highlighted region and confirms it matches what was searched for. Without this step, a wrong-match highlight looks correct in the screenshot but proves the wrong thing. If verification fails, the highlighted region contains different text than expected — re-run with `--mode find` and pick the right match.

### Handling text not found
If a value isn't found via `search_for()`:
- Try variations: with/without commas, with/without decimal points, with/without dollar signs, with/without parentheses for negative values
- Try nearby pages (off-by-one is common with PDF page indexing)
- As a fallback, search the full page text with `page.get_text()` and use regex to confirm the value exists, then use a broader crop without a precise highlight

### Negative values in financial documents
Financial PDFs often format negatives as `(3,000.)` or `-3,000` or `3,000-`. Try multiple format variations when searching. The script automatically tries several common variations.

### Crop sizing
- Default context of 80 PDF points above and below gives ~3-4 lines of surrounding text
- For dense forms (tax returns, financial statements, invoices), 60-80 points works well
- For documents with larger text (contracts, letters), increase to 100-120 points
- Always include the full page width (or nearly so) to preserve document structure

### Scale factor
- Scale 3 (default) produces crisp, readable images at typical screen sizes
- Scale 2 is acceptable for simpler documents
- Don't go below 2 or text becomes hard to read

### Scanned PDFs and OCR
Not all PDFs have embedded text layers. Scanned documents, photographed receipts, and older PDFs may be image-only — `search_for()` returns nothing on these.

Add `--ocr` to enable Tesseract OCR fallback. The script automatically detects whether a page has a text layer and only runs OCR when needed:

```bash
python3 extract_proof.py --pdf scanned_receipt.pdf --search "147.50" --mode find --ocr
```

**Requirements:** Tesseract must be installed on the system (`apt-get install tesseract-ocr` or `brew install tesseract`). OCR results are slightly less precise than native text search, so confidence is capped at "medium" for OCR matches.

**When to use `--ocr`:**
- The PDF is a scan of a paper document
- `--mode find` returns no results but you know the value is on the page
- The PDF was generated from a photo or fax

**When NOT to use `--ocr`:**
- The PDF was generated digitally (most modern tax forms, invoices, contracts) — normal search will be faster and more accurate

## Examples

<!-- Add new examples by appending to this section -->

### Financial / Tax

### Financial / Tax
**User says:** "Double-check these capital loss carryover values against my 2024 return and show me proof"

**What to do:**
1. Identify the values and which form lines they come from
2. For each value, `find` → `verify` → extract a cropped screenshot
3. Generate the HTML proof page with summary table + proof cards + confidence badges
4. Save to the workspace and share the link

**Tips:** Tax forms often have the same number in instruction text and in the actual field. Use `--prefer right` (default) since form values are typically in the right column. Always `find` first.

### Legal / Contract Review
**User says:** "Verify the indemnification cap and termination notice period in the signed contract match what we agreed to"

**What to do:**
1. Search the contract PDF for the specific dollar amount and day count
2. Create proof screenshots from the relevant sections
3. Generate a proof page showing both values with their surrounding contract language

**Tips:** For contracts, use `--prefer first` since values appear inline in paragraphs, not in right-aligned columns. Increase `--context 120` to capture more surrounding language.

### Invoice Reconciliation
**User says:** "Confirm the invoice total and PO number match across the vendor invoice and our purchase order PDFs"

**What to do:**
1. Search both PDFs for the matching values
2. Create proof screenshots from each document
3. Generate a proof page that shows both sources, making discrepancies obvious

**Tips:** When comparing across documents, use the same `--context` and `--scale` for visual consistency in the proof page.

### Insurance / Claims
**User says:** "Show me where in the policy document it states the coverage limit and deductible"

**What to do:**
1. Search the policy PDF for the specific amounts
2. Crop the relevant sections with highlights
3. Generate proof cards with the surrounding policy language visible

### Scanned Receipts / Legacy Documents
**User says:** "Find the total on this scanned receipt and show me proof"

**What to do:**
1. Run with `--ocr` flag since scanned receipts have no text layer
2. OCR may be less precise — use `--mode verify` to cross-check
3. If OCR fails, increase the scan DPI or try searching for just the numeric portion without formatting

### Academic / Research
**User says:** "Verify these statistics from the methodology section match what's cited in the results"

**What to do:**
1. Search the PDF for the specific statistical values (p-values, sample sizes, etc.)
2. Create proof screenshots from both sections
3. Side-by-side proof page highlights discrepancies between cited and reported values
