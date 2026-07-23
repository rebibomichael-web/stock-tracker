# NYC DOE Record Anonymizer (v2.1)

Pseudonymizes NYC DOE student permanent-record PDFs **on your machine** before
they are uploaded to Claude, then restores real names into the returned
outputs (txt/csv/xlsx/pdf). Real PII never leaves the machine: the tool, the
mapping file, and all reports are local-only.

v2 (2026-07-23) replaced v1's line-regex harvest, which silently found 0/133
students on the real senior export (its regexes were validated against a
different extractor's whitespace) and whose self-check only scanned for
values already in the mapping, producing a vacuous PASS on an empty harvest.

v2.1 (same day, after an adversarial multi-lens review of v2 against the real
document shapes in the handoff) closed a further set of silent-leak paths
v2's own design had reintroduced in narrower form — see "What v2.1 fixed"
below. Every fix has a regression test in `test_anonymizer.py` or
`--selftest` reproducing the original failure.

## Install / run

```bash
pip install pymupdf              # required
pip install openpyxl             # only for xlsx restore
pip install tkinterdnd2          # optional drag-and-drop GUI

python3 record_anonymizer.py Records.pdf --expect 133   # -> Records_ANON.pdf
python3 record_anonymizer.py --restore Analysis.xlsx    # -> Analysis_RESTORED.xlsx
python3 record_anonymizer.py                            # GUI
python3 record_anonymizer.py --selftest                 # no PDF libs needed
python3 test_anonymizer.py                              # full E2E on synthetic data
```

Always pass `--expect N` when you know the student count (senior export: 133;
full export: 438) — it becomes a hard check.

## What gets pseudonymized

Student name + OSIS ID (one combined redaction), DOB, phone, address, parent,
counselor (header fields), and teacher names in course rows. `Ofcl`, admit /
discharge / graduation dates, marks, credits, course and exam data are
untouched — the anonymized file still parses identically in the
transcript-to-excel pipeline (this is a hard, checked invariant — see below).

Aliases (deterministic, stable across runs and files, keyed by real OSIS in
the mapping): students `L001XX, F001XX` with IDs from `900000001`; phones from
`8000000001` (a leading digit different from the ID base — see "ID/phone
namespace collision" below); DOBs in 1900–1909 (unique per student);
addresses `ADDR001X`; parents `PL001XX, PF001XX`; counselors `C01CNSL`
(disjoint suffix namespace); teachers `TCH001`, with a trailing single-letter
initial preserved verbatim (`O'BRIEN M` → `TCH003 M`). The initial is kept
deliberately: the downstream parser captures it as the "mark" on
modifier-mark rows (`B+`, `92**`), so changing the letter would change parsed
output; a bare initial with the surname fully aliased identifies nobody.

## Verification (all hard requirements)

1. **Harvest floor** — every page bearing a `Name / ID` label must yield a
   (name, OSIS) pair; `--expect` additionally pins the unique-student count.
   On shortfall the run FAILs **before writing any output**.
2. **Field harvesting is label-bounded, not distance-bounded** — a detached
   or wrapped Address/Parent/Counselor value is found by walking forward
   from its label row-by-row until the next label, the course-table anchor,
   or the header column runs out — never a fixed point tolerance that can
   silently undershoot a value one row further down, or a value that
   legitimately wraps onto a second physical line.
3. **Residual scan** — the saved output is re-extracted (page text **and**
   PDF metadata/bookmark titles) and searched for every real value in the
   mapping, whitespace-tolerant. A case-insensitive-only match **FAILs**
   too — a value the tool has positively identified as real PII is not an
   ambiguity just because its capitalization differs somewhere in the file.
4. **Pattern-class scan** — shapes, not just known values: `LAST, FIRST`
   strings (including 2-letter surnames and single-initial given names —
   `NG, KEVIN`, `RIVERA, P`, unicode surnames like `PEÑA, JOSÉ`) outside the
   alias namespace, Title-Case `First Last` strings (the documented mixed-case
   Parent shape), street-address shapes, DOB-range dates, 9/10-digit numbers
   outside the synthetic ranges. Class hits FAIL; NYC-zip-shaped coincidences
   warn. `--allow-pattern` whitelists a legitimate string, but **only by
   exact match** of the full flagged text — a substring match would silently
   exempt any real PII hit that merely contains the whitelisted fragment.
5. **Alias presence** — every PII field harvested on a page (not just
   name/ID) must show its alias in the output, catching a redaction whose
   replacement text failed to fit and was never drawn.
6. **Data invariance** — page count unchanged; course rows token-identical
   apart from the teacher alias; exam rows byte-identical; **and** the
   non-PII header fields (`Ofcl`, Admit/Discharge/Graduation Date, Grade
   Level, Status, Cumulative Average) are diffed original-vs-anonymized and
   must match exactly — this is the general form of a defect this tool hit
   once in testing (a redaction rect silently clipped a neighboring `Ofcl :`
   field), now a permanent per-run guarantee instead of only being checked
   by the synthetic test suite.

On any failure the output is renamed `*_ANON_FAILED.pdf` (or not written at
all) and the report says `DO NOT UPLOAD`. **Reports never quote a real value
verbatim** — failure messages are masked (`R****************`) since a FAILed
run is exactly when a user is likely to paste the report somewhere for help.
Ambiguous course rows are redacted permissively and logged (privacy over data
fidelity).

Any exception during redaction/verification — including Ctrl-C — leaves no
file under the clean `_ANON.pdf` name: output is written to a `.partial` name
first and only renamed to `_ANON.pdf` after every check PASSes, or to
`_ANON_FAILED.pdf` otherwise.

## Files this tool writes — all local-only

- `anonymizer_mapping.json` — real↔alias table, **contains real PII by
  design**. Never commit or upload it; do back it up (without it, returned
  outputs cannot be restored). Lives beside the script unless `--mapping`.
  Written via `anonymizer_mapping.json.tmp` + atomic rename; the `.tmp`
  suffix is separately covered by `.gitignore` (a bare `*mapping*.json`
  pattern does not match a `.json.tmp` suffix).
- `*_ANON_report.txt` — verification report. Real values in it are masked,
  but treat it as local-only regardless.
- `*_ANON.pdf` — the ONLY file that leaves the machine, and only on PASS.

The `.gitignore` in this directory covers all of the above, plus the
`.partial` work file and any stray `*.tmp`.

## Restore direction

Dictionary substitution only (aliases are unique tokens, `\b`-anchored so one
can't match inside an unrelated longer number, longest-first so a bare token
never eats a longer alias's match), never via an LLM.

- **CSV** is restored CSV-aware (parsed and re-quoted via the `csv` module):
  a plain-text substitution would insert an unquoted comma wherever an alias
  restores to a comma-bearing real value (`LAST, FIRST` names, a counselor
  string), silently shifting every later column.
- **xlsx** is rewritten cell-by-cell with openpyxl, handling non-string cell
  types: spreadsheet writers commonly store all-digit alias IDs/phones as
  numbers and DOB aliases as dates, not strings.
- **PDF** restore re-draws real values into the alias's redacted box, growing
  the redaction out to the next surviving word first (not just the original
  word's own bounding box) so a real value wider than its alias can't leave a
  leftover glyph — e.g. inter-word whitespace — sitting inside the drawn
  text, which a position-based extractor could otherwise read as a spurious
  word-break invisible to a quick look at the PDF. Adjacent occurrences on
  consecutive lines are both restored (substring suppression uses rect
  *containment*, not any-overlap, which used to also suppress genuine
  neighboring hits at normal line spacing).
- **Every format's restore reports issues explicitly** — `restore_file()`
  returns `(path, issues)`; a non-empty list means either an alias-shaped
  token remains after restore (stale mapping, case mismatch, an unsupported
  cell format) or — PDF only — a real value was too long to redraw into its
  alias's box and that field is now genuinely **blank**, not just aliased,
  in the output. Both the CLI and the GUI surface a non-empty `issues` list
  prominently (exit code 1 / a warning dialog), not just a mid-stream log
  line — this is exactly the moment a user could otherwise miss that part of
  their document silently didn't come back.

## Known limitations

- **Mapping evolution has no history.** `Mapping.student()` overwrites a
  student's `real` field values (address, phone, etc.) with whatever was
  harvested most recently. If the SAME OSIS is re-anonymized later from a
  different export with updated values, restoring an OLDER output against
  the UPDATED mapping substitutes the newer real value into a document that
  was written about the old one. There is no per-value versioning — back up
  the mapping file before re-running against a materially different export
  if old outputs may still need restoring.
- **A wrapped alias could theoretically draw twice on PDF restore.** If a
  drawn alias were long enough to visually wrap across a line break in some
  future output, `search_for` would return one rect per line fragment and
  each would independently draw the full real value. Low real-world risk —
  aliases are deliberately short — and not implemented: detecting "one
  occurrence split by a line wrap" vs. "two genuinely separate occurrences"
  from rects alone is ambiguous.
- **The Title-Case `First Last` pattern-class scanner is necessarily
  heuristic** against a document format this tool has not been run against
  for real. It scrubs the specific boilerplate phrases documented/derived
  from the handoff and the downstream parser's own regexes (`Student
  Permanent Record`, `Admit Date`, `Cumulative Average`, etc.) before
  scanning, but the real export may use different exact wording for other
  boilerplate. Expect the **first real run** to need a few `--allow-pattern`
  additions for legitimate mixed-case strings the scanner doesn't yet know
  about — that is the intended, safe failure mode (a loud FAIL asking for a
  whitelist), not a bug to work around by disabling the scanner.
- **Course-row column-boundary geometry is validated against the documented
  layout, not the real 496-page export.** A left-column course row whose
  credits token crosses `x≈304` is detected and merged back into one row
  (regression test included) when the right-column fragment doesn't itself
  look like a new course/exam record; a page whose real layout differs
  enough from what's documented could still defeat this heuristic. Validate
  against the real file before fully trusting unattended runs at scale.
- Teachers with 3+ word names: the transcript-to-excel parser already
  mis-parses such rows in the *original* file (its teacher regex allows at
  most "WORD X"), so parsed output for those rows differs between original
  and anonymized — the anonymized side is actually the cleaner parse. The
  in-tool token-level comparer is the authoritative invariance check.
- The downstream parser drops rows whose mark ends in `**` or `+`/`-` with a
  single-word teacher — equally in both files (pre-existing; worth fixing in
  the skill's `parse_transcripts.py` someday: allow `\*{0,2}` and `[+-]` in
  its mark validation).
- Scanned/image PDFs are out of scope (no OCR); the source exports are text.
- Optional hardening not yet wired: Microsoft Presidio as an independent
  second-pass auditor over the `_ANON.pdf` (handoff §5.5).

## What v2.1 fixed (adversarial review, 2026-07-23)

A 5-lens adversarial workflow review (privacy-leak, real-data-robustness,
API-correctness, restore-correctness, spec-compliance — each finding
independently re-verified by a skeptical refuter before being trusted) found
34 confirmed defects in the initial v2. All are fixed here, most importantly:

- **Non-ASCII teacher names** (`MUÑOZ`, `PEÑA`) silently redacted nothing and
  logged nothing — the mark-anchor gap check was ASCII-only.
- **Detached/wrapped Address, Parent, and Counselor values beyond a fixed
  point-distance window** were silently skipped rather than harvested — real
  content just past the old ±7pt tolerance, or wrapped onto a second line,
  shipped in a PASSed file. Fixed by the label-bounded (not distance-bounded)
  forward search described above.
- **A case-variant of a known real name** (`Rogueberg, Zinnia` vs. the
  harvested `ROGUEBERG, ZINNIA`) only warned, not FAILed.
- **Course rows crossing the `x≈304` column split** left the teacher
  unredacted, and the invariance comparer was symmetrically blind to it.
- **An ID/phone alias namespace collision**: with the old bases
  (`900000000`/`9000000000`), a phone alias truncated to 9 digits could equal
  a *different* student's ID alias exactly — fixed by using leading digits
  (`9`/`8`) that make the two namespaces structurally disjoint, not just
  distinguishable by length.
- **A wider alias replacing a shorter redacted word could leave a leftover
  glyph — a leftover inter-word space character — spatially inside the newly
  drawn (wider) text.** PyMuPDF's own extraction didn't notice, but the
  position-based extractor the downstream Excel pipeline uses (pdfplumber)
  split the alias mid-word (`TCH008` → `TCH00` + `8`), corrupting parsed
  output. Fixed by extending the redaction rect to the same boundary used for
  the draw, not just the original word's own box.
- **The `--allow-pattern` whitelist matched by substring**, so a legitimate
  short whitelist entry could silently exempt an unrelated real PII hit that
  merely contained it. Now exact-match only.
- **The restore direction had no verification at all** — a real name too
  long to redraw into its alias's box silently left that field blank with no
  error surfaced anywhere. `restore_file()` now returns `(path, issues)` and
  every caller must show a non-empty list prominently.
- Several `xlsx` restore, CSV-comma, digit-alias-boundary, and
  bare-parent-token-introduces-a-comma bugs — see the fix list in
  `record_anonymizer.py`'s section 6-8 docstrings for the complete set.

## Testing

`--selftest` runs pure-function tests (alias spec incl. the disjoint
ID/phone namespaces, mark classification, teacher segmentation incl. non-ASCII
surnames and the documented traps, column-split merge-back, pattern-class
scanner shapes incl. short/unicode names and Title-Case, restore incl. the
digit-boundary and bare-parent-split fixes).

`test_anonymizer.py` builds a synthetic 15-page/14-student fixture — traps
included: shared teacher/student surname, borough first name, hyphenated and
multi-word surnames, name suffix, detached AND wrapped (multi-line) header
blocks, a non-ASCII teacher, a column-boundary-crossing course row, an
attached-colon `Counselor:` label with a single-initial value, a continuation
page, short/long names, full marks vocabulary — and checks the §9 acceptance
criteria end-to-end, including the downstream parser (vendored snapshot),
metadata-leak/case-variant/report-masking regression checks, and hard-FAIL
negative controls. No real data is used anywhere in the tests.
