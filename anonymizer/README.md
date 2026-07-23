# NYC DOE Record Anonymizer (v2)

Pseudonymizes NYC DOE student permanent-record PDFs **on your machine** before
they are uploaded to Claude, then restores real names into the returned
outputs (txt/csv/xlsx/pdf). Real PII never leaves the machine: the tool, the
mapping file, and all reports are local-only.

This is v2 per the 2026-07-23 handoff. v1's line-regex harvest silently found
0/133 students on the real senior export (its regexes were validated against a
different extractor's whitespace) and its self-check only scanned for values
already in the mapping, so an empty harvest produced a vacuous PASS. v2 fixes
both classes of failure:

- **Word-coordinate harvest** — labels and values are located as word boxes
  (`page.get_text("words")` + y-band clustering), so detection is independent
  of label whitespace and line structure, handles detached Address/Parent
  blocks, and shares one coordinate space with the redaction step.
- **Hard verification, never vacuous** — see below. A run that cannot prove
  the output is clean FAILs loudly and quarantines the output.

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
transcript-to-excel pipeline.

Aliases (deterministic, stable across runs and files, keyed by real OSIS in
the mapping): students `L001XX, F001XX` with IDs from `900000001`; phones from
`9000000001`; DOBs in 1900–1909 (unique per student); addresses `ADDR001X`;
parents `PL001XX, PF001XX`; counselors `C01CNSL` (disjoint suffix namespace);
teachers `TCH001`, with a trailing single-letter initial preserved verbatim
(`O'BRIEN M` → `TCH003 M`). The initial is kept deliberately: the downstream
parser captures it as the "mark" on modifier-mark rows (`B+`, `92**`), so
changing the letter would change parsed output; a bare initial with the
surname fully aliased identifies nobody.

## Verification (all hard requirements)

1. **Harvest floor** — every page bearing a `Name / ID` label must yield a
   (name, OSIS) pair; `--expect` additionally pins the unique-student count.
   On shortfall the run FAILs **before writing any output**.
2. **Residual scan** — the saved output is re-extracted and searched for every
   real value in the mapping (whitespace-tolerant; case-variants warn).
3. **Pattern-class scan** — shapes, not just known values: `LAST, FIRST`
   strings outside the alias namespace, street-address shapes, DOB-range
   dates, 9/10-digit numbers outside the synthetic ranges. Class hits FAIL;
   NYC-zip-shaped coincidences warn. Legit strings (e.g. a school name
   containing a comma) can be whitelisted with `--allow-pattern`.
4. **Alias presence** — each harvested page must show its alias name/ID in the
   output (catches redactions that dropped text the parser needs).
5. **Data invariance** — page count unchanged; course rows token-identical
   apart from the teacher alias; exam rows byte-identical.

On any failure the output is renamed `*_ANON_FAILED.pdf` (or not written at
all) and the report says `DO NOT UPLOAD`. Ambiguous course rows are redacted
permissively and logged (privacy over data fidelity).

## Files this tool writes — all local-only

- `anonymizer_mapping.json` — real↔alias table, **contains real PII by
  design**. Never commit or upload it; do back it up (without it, returned
  outputs cannot be restored). Lives beside the script unless `--mapping`.
- `*_ANON_report.txt` — verification report; permissive-row log lines can
  quote course rows, so treat it as local-only too.
- `*_ANON.pdf` — the ONLY file that leaves the machine, and only on PASS.

The `.gitignore` in this directory covers all of the above.

## Restore direction

Dictionary substitution only (aliases are unique tokens; longest-first), never
via an LLM. xlsx is rewritten cell-by-cell with openpyxl. Bare alias tokens
(`F001XX`, `TCH001`, `PL001XX`) restore too, in case an output splits a name.
PDF restore re-draws real values into the alias boxes and may shrink very long
names to fit.

## Known limitations

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

## Testing

`--selftest` runs pure-function tests (alias spec, mark classification,
teacher segmentation incl. the documented traps, scanner shapes, restore).
`test_anonymizer.py` builds a synthetic 13-page/12-student fixture — traps
included: shared teacher/student surname, borough first name, hyphenated and
multi-word surnames, name suffix, detached header blocks, continuation page,
short/long names, full marks vocabulary — and checks the §9 acceptance
criteria end-to-end, including the downstream parser (vendored snapshot) and
hard-FAIL negative controls. No real data is used anywhere in the tests.
