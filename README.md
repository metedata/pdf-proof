# pdf-proof

A Claude skill that generates visual proof pages from PDF documents. Search for specific values, crop precise screenshots with highlights, cross-check via text readback, and assemble everything into a clean HTML proof page.

Built for anyone who needs to verify numbers against source PDFs — tax preparers, accountants, lawyers, auditors, researchers, or anyone reconciling data across documents.

## What it does

Given a PDF and a value to verify, the skill:

1. **Finds** the exact text coordinates using PyMuPDF's text search
2. **Crops** a readable section of the page around the match
3. **Highlights** the value with a translucent green highlight (no overlapping borders, no guesswork)
4. **Verifies** by reading text back from the highlighted region to confirm correctness
5. **Scores confidence** — single match = high, auto-selected from multiple = medium, OCR or failed verification = low
6. **Assembles** an HTML proof page with a summary table and per-value proof cards

The result is a standalone HTML file with embedded screenshots — a visual audit trail you can keep, share, or attach to a filing.

## Installation

You need the **entire folder** (not just the SKILL.md). The script, template, and evals are all part of the skill.

### Claude Desktop / Cowork

1. Download the zip
2. Open **Settings > Customize > Skills**
3. Click **Upload skill** and select the zip

The app extracts and installs it automatically. Code execution must be enabled (Settings > Capabilities).

### Claude Code (CLI)

Copy the folder to either location:

```bash
# Global (available in all projects)
cp -r pdf-proof ~/.claude/skills/

# Project-specific (available in this repo only)
cp -r pdf-proof .claude/skills/
```

Skills are picked up automatically — no restart needed.

### Sharing with others

Three ways to distribute:

- **Zip file** — share the zip directly; recipients install via the methods above
- **Git** — commit the folder to `.claude/skills/` in a repo; anyone who clones the repo gets the skill
- **Plugin** — bundle into a Claude Code plugin for marketplace distribution

### Dependencies

The script auto-installs PyMuPDF and Pillow on first run. For OCR support (scanned PDFs):

```bash
# macOS
brew install tesseract

# Ubuntu/Debian
apt-get install tesseract-ocr
```

### Use it

Ask Claude to verify values against a PDF:

> "Double-check these values against my tax return and show me proof: Form 1040 line 15, Schedule D line 7"

> "Verify the invoice total matches the PO"

> "Show me where in the contract it says the termination notice is 30 days"

> "Find the total on this scanned receipt"

The skill triggers on phrases like "confirm", "verify", "prove", "double-check", "show me proof", "where does this come from", "screenshot proof", or any request to trace a value back to a source PDF.

## Standalone script usage

The `extract_proof.py` script works independently of Claude:

```bash
# Find where a value appears in a PDF
python3 scripts/extract_proof.py --pdf invoice.pdf --search "1,250.00" --mode find

# Extract a cropped screenshot with highlight
python3 scripts/extract_proof.py --pdf invoice.pdf --search "1,250.00" \
  --page 1 --output proof.png --highlight value

# Verify: extract + cross-check the highlighted text
python3 scripts/extract_proof.py --pdf invoice.pdf --search "1,250.00" \
  --page 1 --output proof.png --mode verify --json

# OCR fallback for scanned documents
python3 scripts/extract_proof.py --pdf scan.pdf --search "147.50" \
  --mode verify --ocr --json
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--pdf` | (required) | Path to the PDF file |
| `--search` | (required) | Text to find and highlight |
| `--mode` | `extract` | `find`, `extract`, or `verify` |
| `--page` | auto | 1-indexed page number |
| `--output` | `proof.png` | Output PNG path |
| `--highlight` | `value` | `value` (tight box), `row` (full-width band), `none` |
| `--context` | `80` | Vertical context in PDF points above/below match |
| `--scale` | `3` | Render scale factor (3 = crisp) |
| `--prefer` | `right` | Multi-match selection: `right`, `first`, `last` |
| `--match-index` | auto | Explicit match index (0-based), overrides `--prefer` |
| `--ocr` | off | Enable Tesseract OCR for scanned PDFs |
| `--json` | off | Structured JSON output with confidence + verification |

### Confidence levels

| Level | Meaning |
|-------|---------|
| **high** | Single match on page, exact text, verification passed |
| **medium** | Auto-selected from multiple matches, or matched via formatting variation, or OCR |
| **low** | Verification failed (readback doesn't match search text) — needs manual review |

## How it handles tricky cases

**Multiple matches on the same page:** A value like "3,000" might appear in both instruction text and the actual form field. The script defaults to `--prefer right` (rightmost match), which works well for structured forms where values are in right-hand columns. For narrative documents, use `--prefer first` or `--match-index N`.

**Formatting variations:** Automatically tries common variants — with/without commas, dollar signs, parentheses, periods. If a variation matches instead of the exact text, confidence drops to medium.

**Scanned PDFs:** With `--ocr`, the script detects pages without embedded text and runs Tesseract OCR automatically. OCR matches are capped at medium confidence.

**Text readback verification:** In `verify` mode, after finding and highlighting, the script reads the actual text from the highlighted PDF region and compares it against the search term. If they don't match, confidence drops to low and a warning is printed.

## File structure

```
pdf-proof/
  SKILL.md                    # Skill instructions for Claude
  scripts/
    extract_proof.py          # Core extraction script
  assets/
    proof_template.html       # HTML template for proof pages
  evals/
    evals.json                # Test cases (replace file paths with your own)
```

## Contributing examples

The `## Examples` section at the bottom of SKILL.md is designed to grow. To add a new use case:

1. Add a new `### Category Name` subsection
2. Include a "User says" prompt and "What to do" steps
3. Add any relevant "Tips" for that document type
4. Optionally add a matching eval case in `evals/evals.json`

## License

MIT
