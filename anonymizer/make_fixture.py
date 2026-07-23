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
  * detached Address / Parent values (offset so they extract as separate
    blocks); Address blank on page 1 but populated later (v1's blind spot)
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
]

# Teachers: MARLOWE shares a surname with student #8; BROOKLYN never a teacher.
TEACHERS = ["NGUYEN T", "MARLOWE", "O'BRIEN M", "QUINCEY", "SMITH S",
            "VELAZQUEZ-RUZ", "OYELARAN B"]

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


def _header(page, fitz, s, page_no, page_total, y0=62):
    """Gray box + header lines with the documented whitespace shapes."""
    last, first, osis, dob, phone, address, parent, counselor, detached = s
    page.draw_rect(fitz.Rect(10, y0 - 8, 298, y0 + 78), fill=(0.85, 0.85, 0.85),
                   color=None)
    t = lambda x, y, txt: page.insert_text((x, y), txt, fontname="cour",
                                           fontsize=7)
    t(30, 30, SCHOOL)
    t(30, 42, "Student Permanent Record")
    t(14, y0, "Name / ID  :  %s, %s  /  %s" % (last, first, osis))
    if address and not detached:
        t(14, y0 + 12, "Address  :  %s" % address)
    else:
        t(14, y0 + 12, "Address  :")
        if address and detached:
            # detached block: offset beyond the band tolerance (3pt) so only
            # the widened detached-value search can find it
            t(72, y0 + 16.5, address)
    t(14, y0 + 24, "Ph# : %s      Ofcl : 3C3" % phone)
    t(14, y0 + 36, "DOB  :  %s" % dob)
    if detached:
        t(14, y0 + 48, "Parent:")
        t(58, y0 + 49.1, parent)
        t(150, y0 + 48, "Counselor : %s" % counselor)
    else:
        t(14, y0 + 48, "Parent: %s   Counselor : %s" % (parent, counselor))
    t(14, y0 + 60, "Admit Date : 09/07/2022   Grade Level : 12   Status : A")
    t(270, PAGE_H - 20, "Page %d of %d" % (page_no, page_total))


def _course_line(school, code, name, mark, teacher, year, term):
    parts = ["%d / %d" % (year, term), school, code, name, mark]
    if teacher:
        parts.append(teacher)
    parts.append("1.00/1.00")
    return "  ".join(parts)


def build_fixture(out_path):
    import fitz
    doc = fitz.open()
    total_pages = 0
    for i, s in enumerate(STUDENTS):
        two_page = (i == 4)
        total = 2 if two_page else 1
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        total_pages += 1
        _header(page, fitz, s, 1, total)
        page.insert_text((LEFT_X, 160), "Course", fontname="cour", fontsize=7)

        y = 176
        for j, (school, code, name, mark) in enumerate(COURSES[:8]):
            teacher = "" if mark in ("CR*", "S", "NS*", "P*") else \
                TEACHERS[(i + j) % len(TEACHERS)]
            page.insert_text((LEFT_X, y), _course_line(
                school, code, name, mark, teacher, 2024, 1 + j % 2),
                fontname="cour", fontsize=7)
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
            _header(page, fitz, s, 2, total)
            y = 176
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
