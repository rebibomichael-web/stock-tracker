#!/usr/bin/env python3
"""
Synthetic NYC-DOE-style permanent-record PDF for testing record_anonymizer.

Contains ONLY invented placeholder people — no real student data. The layout
mirrors the documented format parameters (handoff §2): two-column flow split
at x≈304, gray header box upper-left, 'Course' anchor word at x<20, header
label shapes with load-bearing whitespace, continuation pages with repeated
header but no 'Course' anchor, 'Page X of Y' footers, and the §7 traps:

  * a teacher and a student sharing a surname (MARLOWE)
  * a student whose first name is a borough (BROOKLYN) that also appears in
    the school name and in addresses
  * hyphenated and multi-word surnames; a name suffix ('..., TESSERA I')
  * a very short name (font-fit stress) and a very long one
  * detached Address / Parent values, INCLUDING genuine multi-line wraps
    (a value continuing onto a second physical row, well beyond any fixed
    point-distance window); Address blank on page 1 but populated later
  * a non-ASCII teacher surname (MUÑOZ) in a course row
  * a course row whose trailing credits token crosses the x=304 column
    split (long course name + short teacher near the boundary)
  * a 'Counselor:' label with the colon attached to the word (no space),
    holding a single-initial 'LAST, I' value — the counselor shape the
    project's own spec documents for the full export
  * marks vocabulary: numeric, starred, weighted '**', CR*/P*/NS*, letter
    grades, and a stray 'S' artifact row

Usage: python3 make_fixture.py OUT.pdf   (also importable: build_fixture)
"""

import sys

PAGE_W, PAGE_H = 612, 792
LEFT_X = 12
RIGHT_X = 308
SCHOOL = "MIDWOOD TESTING HS AT BROOKLYN"

# (last, first, osis, dob, phone, address, parent, counselor, detached)
STUDENTS = [
    ("AARDWOLF", "QUIMBY", "212000001", "03/14/2008", "7185550001",
     "", "AARDWOLF, PETUNIA", "", False),
    ("VARGA-QUINN", "BROOKLYN", "212000002", "07/02/2008", "7185550002",
     "88 OCEAN PKWY 3E BROOKLYN NY 11218", "VARGA-QUINN, ORSOLYA", "", False),
    ("DE LA CRUZ MONTOYA", "XIMENAZ", "212000003", "11/23/2007", "7185550003",
     "451 TESTING AVE BROOKLYN NY 11230", "Ramona De La Cruz", "", True),
    ("OKONKWO", "TESSERA I", "212000004", "01/30/2008", "7185550004",
     "77 FAUX ST 2F BROOKLYN NY 11226", "OKONKWO, CHUKS", "", False),
    ("ZYLBERSTEIN", "KAVELLE", "212000005", "05/05/2008", "7185550005",
     "1200 MOCKERY BLVD BROOKLYN NY 11210", "ZYLBERSTEIN, DOV",
     "RIVERA, P", False),                    # two-page record
    ("QO", "ZA", "212000006", "09/09/2008", "7185550006",
     "", "QO, BO", "", False),               # shortest name: font-fit stress
    ("PLASKETT", "EMBERLYNNE-AUGUSTINA", "212000007", "02/17/2008",
     "7185550007", "9 LONGWINDED PL 11A BROOKLYN NY 11215",
     "PLASKETT-VANOSTRAND, SERAPHINELLA", "", True),
    ("MARLOWE", "FENN", "212000008", "12/12/2007", "7185550008",
     "300 SHARED NAME RD BROOKLYN NY 11235", "MARLOWE, IDRIS", "", False),
    ("NKEMDIRIM", "OLAMIDE", "212000009", "04/01/2008", "7185550009",
     "16 QUIZZICAL CT BROOKLYN NY 11224", "Olamide Nkemdirim Sr", "", False),
    ("TARKOVSKY", "WREN", "212000010", "06/18/2008", "7185550010",
     "", "TARKOVSKY, MILA", "RIVERA, P", False),
    ("UMBERSLEIGH", "COBALT", "212000011", "08/08/2008", "7185550011",
     "2020 IMAGINARY LN BROOKLYN NY 11229", "UMBERSLEIGH, JET", "", False),
    ("XANTHOPOULOS", "DELPHI", "212000012", "10/31/2007", "7185550012",
     "5 MADEUP TER BROOKLYN NY 11214", "XANTHOPOULOS, ARI", "", True),
    # --- v2 regression-test additions (per 2026-07-23 adversarial review) ---
    ("NAKAGAWA-BRENNTUCH", "ISOLDINE", "212000013", "03/03/2008", "7185550013",
     "40 WRAPPING WAY 4B", "MADIGAN, P", "", True),   # multi-line address+parent
    ("QUILLFEATHER", "BAX", "212000014", "04/04/2008", "7185550014",
     "60 SPECIAL AVE BROOKLYN NY 11201", "QUILLFEATHER, RAE", "NG, P",
     False),                                          # attached-colon counselor
]

# Teachers: MARLOWE shares a surname with student #8; BROOKLYN never a teacher.
# MUÑOZ carries a non-ASCII surname (handoff-adjacent real-world trap).
TEACHERS = ["NGUYEN T", "MARLOWE", "O'BRIEN M", "QUINCEY", "SMITH S",
            "VELAZQUEZ-RUZ", "OYELARAN B", "MUÑOZ"]

COURSES = [  # (school, code, name, mark) — credits appended per row
    ("435", "EES87", "ENG 7", "91"),
    ("435", "MES87", "ALG 2", "85"),
    ("435", "HES87", "US HIST", "78*"),
    ("435", "SES87", "CHEM", "92**"),
    ("435", "PES87", "PHYS ED", "P*"),
    ("435", "AES87", "STUDIO ART", "B+"),
    ("400", "TRF01", "TRANSFER PHYS E", "CR*"),
    ("435", "MUS87", "ORCHESTRA", "S"),      # stray-artifact mark, no teacher
    ("435", "LES87", "SPANISH 3", "65"),
    ("435", "SCL87", "CHEM LAB", "NS*"),
    ("435", "EES88", "ENG 8", "88"),
    ("435", "GES88", "GLOBAL HIST", "55"),
]

EXAMS = [
    ("SXRK", "ALG2/TRIG REG", "85"),
    ("SXRE", "ENGLISH REG", "91"),
    ("SXRU", "US HIST REG", "78"),
]

# Multi-line values for the two new detached-block students (index -> lines).
WRAPPED_ADDRESS = {12: ["40 WRAPPING WAY 4B", "BROOKLYN NY 11223"]}
WRAPPED_PARENT = {12: ["Madigan-Isoldine", "Nakagawa-Brenntuch"]}
# Column-crossing course row (index -> (course_name, mark, teacher)); placed
# as an extra explicit-coordinate row so its credits token is guaranteed to
# land at x >= COLUMN_SPLIT_X regardless of font metrics.
COLUMN_CROSS_ROW = {0: ("ADVANCED PLACEMENT ENGLISH LITERATURE", "88", "ZAXWORTHY")}
COUNSELOR_ATTACHED = {13}  # render "Counselor:" (no space) for these indices


def _header(page, fitz, s, idx, page_no, page_total, y0=62):
    """Gray box + header lines with the documented whitespace shapes.

    Uses a sequential y-cursor (not fixed offsets) so that a wrapped value
    pushes every following header line down by a full line-height, exactly
    as a real PDF renderer would — a fixed-offset layout let a wrapped
    line's continuation sit unrealistically close to (and get band-
    clustered with) the next label, which isn't a real-document shape.
    """
    last, first, osis, dob, phone, address, parent, counselor, detached = s
    LINE = 12
    t = lambda x, y, txt: page.insert_text((x, y), txt, fontname="cour",
                                           fontsize=7)
    t(30, 30, SCHOOL)
    t(30, 42, "Student Permanent Record")

    y = y0
    t(14, y, "Name / ID  :  %s, %s  /  %s" % (last, first, osis))
    y += LINE

    addr_lines = WRAPPED_ADDRESS.get(idx)
    if addr_lines:
        # Wraps onto a second PHYSICAL line, well beyond any fixed-point
        # detached window — only a label-bounded forward search finds it.
        t(14, y, "Address  :  %s" % addr_lines[0])
        y += LINE
        t(20, y, addr_lines[1])
        y += LINE
    elif address and not detached:
        t(14, y, "Address  :  %s" % address)
        y += LINE
    else:
        t(14, y, "Address  :")
        y += LINE
        if address and detached:
            # On its OWN following line (not a sub-offset within the same
            # line slot): a 7pt glyph's box extends ~6.5pt above and ~2pt
            # below its baseline, so a value offset by only a few points
            # from its label can still vertically overlap the label above
            # AND the next header field below at once — there isn't room
            # for both within a single 12pt line gap. A full line clears
            # both, and the detached-vs-wrapped distinction is still
            # exercised (this consumes exactly one extra line, not two).
            t(72, y, address)
            y += LINE
        y += LINE

    t(14, y, "Ph# : %s      Ofcl : 3C3" % phone)
    y += LINE
    t(14, y, "DOB  :  %s" % dob)
    y += LINE

    parent_lines = WRAPPED_PARENT.get(idx)
    if parent_lines:
        # Genuinely detached AND wraps to a second line — both the
        # "not on the label's row at all" and "continues past one row"
        # shapes at once.
        t(14, y, "Parent:")
        if idx in COUNSELOR_ATTACHED:
            t(150, y, "Counselor:%s" % counselor)
        elif counselor:
            t(150, y, "Counselor : %s" % counselor)
        y += LINE
        t(58, y, parent_lines[0])
        y += LINE
        t(58, y, parent_lines[1])
        y += LINE
    elif detached:
        t(14, y, "Parent:")
        # Deliberately unconditional (even when counselor==""): this
        # renders an empty "Counselor :" label sharing a row with the
        # equally-blank "Parent:" label, both needing forward resolution
        # from the SAME next row — regression coverage for a real bug
        # where both blank labels grabbed the same forward content
        # (corrupting the redaction rect union and clipping "Ofcl :"
        # beneath it). harvest_page() must assign it to only one label,
        # by x-proximity, not to both.
        if idx in COUNSELOR_ATTACHED:
            t(150, y, "Counselor:%s" % counselor)
        else:
            t(150, y, "Counselor : %s" % counselor)
        y += LINE
        t(58, y + 1.1, parent)
        y += LINE
    else:
        if idx in COUNSELOR_ATTACHED:
            t(14, y, "Parent: %s   Counselor:%s" % (parent, counselor))
        else:
            t(14, y, "Parent: %s   Counselor : %s" % (parent, counselor))
        y += LINE

    t(14, y, "Admit Date : 09/07/2022   Grade Level : 12   Status : A")
    y += LINE
    t(270, PAGE_H - 20, "Page %d of %d" % (page_no, page_total))
    page.draw_rect(fitz.Rect(10, y0 - 8, 298, y + 6), fill=(0.85, 0.85, 0.85),
                   color=None, overlay=False)
    return y


def _course_line(school, code, name, mark, teacher, year, term):
    parts = ["%d / %d" % (year, term), school, code, name, mark]
    if teacher:
        parts.append(teacher)
    parts.append("1.00/1.00")
    return "  ".join(parts)


def _place_row(page, fitz, x0, y, tokens, size=7):
    """Place tokens at explicit, guaranteed x-positions (fixture only) —
    used for the column-crossing course row, where the test needs the
    credits token to land at x >= COLUMN_SPLIT_X deterministically rather
    than hoping natural text flow crosses it.
    """
    x = x0
    space = fitz.get_text_length(" ", fontname="cour", fontsize=size)
    for tok in tokens:
        page.insert_text((x, y), tok, fontname="cour", fontsize=size)
        x += fitz.get_text_length(tok, fontname="cour", fontsize=size) + 2 * space


def build_fixture(out_path):
    import fitz
    doc = fitz.open()
    total_pages = 0
    for i, s in enumerate(STUDENTS):
        two_page = (i == 4)
        total = 2 if two_page else 1
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        total_pages += 1
        header_end = _header(page, fitz, s, i, 1, total)
        anchor_y = max(160, header_end + 12)
        page.insert_text((LEFT_X, anchor_y), "Course", fontname="cour", fontsize=7)

        y = anchor_y + 16
        for j, (school, code, name, mark) in enumerate(COURSES[:8]):
            teacher = "" if mark in ("CR*", "S", "NS*", "P*") else \
                TEACHERS[(i + j) % len(TEACHERS)]
            page.insert_text((LEFT_X, y), _course_line(
                school, code, name, mark, teacher, 2024, 1 + j % 2),
                fontname="cour", fontsize=7)
            y += 10
        cross = COLUMN_CROSS_ROW.get(i)
        if cross:
            cname, cmark, cteacher = cross
            _place_row(page, fitz, LEFT_X, y,
                      ["2024", "/", "1", "435", "EES99", cname, cmark,
                       cteacher, "1.00/1.00"])
            y += 10
        page.insert_text((LEFT_X, y + 4), "Subtotal :  8.00/8.00",
                         fontname="cour", fontsize=7)

        y = 176
        for j, (school, code, name, mark) in enumerate(COURSES[8:]):
            teacher = "" if mark in ("CR*", "S", "NS*", "P*") else \
                TEACHERS[(i + 3 + j) % len(TEACHERS)]
            page.insert_text((RIGHT_X, y), _course_line(
                school, code, name, mark, teacher, 2025, 1 + j % 2),
                fontname="cour", fontsize=7)
            y += 10
        page.insert_text((RIGHT_X, y + 4), "Subtotal :  4.00/4.00",
                         fontname="cour", fontsize=7)
        y += 22
        for code, name, score in EXAMS:
            page.insert_text((RIGHT_X, y), "2025 Term 2  %s  %s  %s"
                             % (code, name, score), fontname="cour", fontsize=7)
            y += 10
        page.insert_text(
            (RIGHT_X, y + 8),
            "Cumulative : Actual Credits / Credits Earned  12.00 / 12.00",
            fontname="cour", fontsize=6)
        page.insert_text((RIGHT_X, y + 18), "Cumulative Average : 84.5%",
                         fontname="cour", fontsize=7)

        if two_page:  # continuation: repeated header, NO 'Course' anchor word
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            total_pages += 1
            header_end2 = _header(page, fitz, s, i, 2, total)
            y = max(176, header_end2 + 16)
            for j, (school, code, name, mark) in enumerate(COURSES[:4]):
                teacher = "" if mark in ("CR*", "S", "NS*", "P*") else \
                    TEACHERS[(i + 5 + j) % len(TEACHERS)]
                page.insert_text((LEFT_X, y), _course_line(
                    school, code, name, mark, teacher, 2023, 1 + j % 2),
                    fontname="cour", fontsize=7)
                y += 10
    doc.save(out_path)
    doc.close()
    return total_pages, len(STUDENTS)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fixture_records.pdf"
    pages, students = build_fixture(out)
    print("wrote %s: %d pages, %d students" % (out, pages, students))
