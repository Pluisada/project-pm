"""Part B: card export and empty template (excel_cards_writer + the two company routes).

Disposable tmp_path identity and company databases; synthetic data only.
"""
import io
import sqlite3
from datetime import date

import pytest
from openpyxl import load_workbook

import excel_contract as contract
from database_targets import company_database_path
from excel_cards_writer import INSTRUCTIONS_SHEET, XLSX_MEDIA_TYPE, build_workbook
from test_company_routes import NOW, rig  # noqa: F401  (rig is a fixture)

HEADERS = list(contract.HEADERS)


def sheet(data, name=contract.SHEET_NAME):
    return load_workbook(io.BytesIO(data))[name]


def table(data):
    ws = sheet(data)
    return [[c.value for c in row] for row in ws.iter_rows()]


def add_cards(path, cards, columns=()):
    con = sqlite3.connect(path)
    try:
        for cid, title, kind in columns:
            con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                        "VALUES (?, 1, ?, ?, ?, ?, ?)", (cid, title, cid, kind, NOW, NOW))
        for card in cards:
            fields = {"column_id": 1, "position": 10 + card["id"], "created_at": NOW, "updated_at": NOW, **card}
            due = fields.pop("next_step_due", None)  # lives in card_bid_details, like the card screen's Next Action Date
            if due is not None:
                con.execute("INSERT INTO card_bid_details (card_id, next_step_due, revenue_currency, created_at, updated_at) "
                            "VALUES (?, ?, 'BRL', ?, ?)", (fields["id"], due, NOW, NOW))
            con.execute(f"INSERT INTO cards ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                        list(fields.values()))
        con.commit()
    finally:
        con.close()


def export(rig, slug="lity", token=None, board=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return rig.client.get(f"/api/companies/{slug}/boards/{board}/excel/export", headers=headers)


def template(rig, slug="lity", token=None, board=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return rig.client.get(f"/api/companies/{slug}/boards/{board}/excel/template", headers=headers)


def admin_export(rig, slug="lity"):
    r = export(rig, slug, rig.e.token("admin@example.com"))
    assert r.status_code == 200
    return r.content


def by_title(data):
    rows = table(data)
    return {row[1]: dict(zip(rows[0], row)) for row in rows[1:]}


# --- contract ---------------------------------------------------------------

def test_template_and_empty_export_share_the_contract_headers(rig):
    t = template(rig, token=rig.e.token("admin@example.com"))
    assert t.status_code == 200
    assert table(t.content) == [HEADERS]
    assert [c.value for c in sheet(t.content)[1]] == HEADERS
    # the seeded card is the only data row of the export, with the same header row
    assert table(admin_export(rig))[0] == HEADERS


def test_template_equals_workbook_with_zero_cards_and_has_instructions(rig):
    t = template(rig, token=rig.e.token("admin@example.com"))
    assert sheet(t.content).max_row == 1
    guide = sheet(t.content, INSTRUCTIONS_SHEET)
    text = " ".join(str(c.value) for row in guide.iter_rows() for c in row)
    assert "deal_total_usd is" not in text and "expected_close_date" not in text
    for needle in ("card_id", "contact_email", "contact_phone", "next_action_date", "USD", "HIGH", "Trade show", "stage", "salesperson_email"):
        assert needle in text


def test_template_has_no_formula_cells(rig):
    wb = load_workbook(io.BytesIO(build_workbook([])))
    for ws in wb:
        assert all(c.data_type != "f" for row in ws.iter_rows() for c in row)


def test_export_headers_in_canonical_order_even_without_cards():
    assert table(build_workbook([])) == [HEADERS]


def test_template_headers_are_exactly_the_expected_ones():
    assert HEADERS == [
        "card_id", "title", "company", "contact", "contact_email", "contact_phone", "details", "deal_value_usd",
        "deal_value_next_year_usd", "next_action_date", "probability_level", "product_line", "lead_source",
        "salesperson_email", "stage",
    ]
    for derived_or_obsolete in ("deal_total_usd", "deal_year", "expected_close_date", "next_step_due"):
        assert derived_or_obsolete not in HEADERS


# --- content ----------------------------------------------------------------

def test_export_fields_and_derived_total(rig):
    path = company_database_path("lity", rig.e.env)
    add_cards(path, [
        {"id": 2, "title": "Full", "company": "Acme", "contact": "Jo", "details": "d", "deal_value": 1000.5,
         "deal_value_next_year": 250.25, "deal_year": 2024, "confidence_level": "HIGH",
         "contact_email": "jo@acme.com", "contact_phone": "+1 555 0100", "next_step_due": "2026-03-04", "product_line": "Chargers", "lead_source": "Referral",
         "salesperson_id": rig.e.admin},
        {"id": 3, "title": "Zero", "deal_value": 0, "deal_value_next_year": 0},
        {"id": 4, "title": "Next only", "deal_value": None, "deal_value_next_year": 75},
    ])
    con = sqlite3.connect(path)
    con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'adm', ?, ?)",
                (rig.e.admin, NOW, NOW))
    con.commit()
    con.close()
    rows = by_title(admin_export(rig))
    full = rows["Full"]
    assert full["card_id"] == 2 and full["deal_value_usd"] == 1000.5 and full["deal_value_next_year_usd"] == 250.25
    assert full["contact_email"] == "jo@acme.com" and full["contact_phone"] == "+1 555 0100"
    assert "deal_total_usd" not in full and "deal_year" not in full and "expected_close_date" not in full
    assert full["probability_level"] == "HIGH" and full["product_line"] == "Chargers"
    assert full["lead_source"] == "Referral" and full["stage"] == "Lead"
    assert full["salesperson_email"] == "admin@example.com"
    assert full["next_action_date"].date() == date(2026, 3, 4)
    assert rows["Zero"]["deal_value_usd"] == 0
    assert rows["Next only"]["deal_value_usd"] is None and rows["Next only"]["deal_value_next_year_usd"] == 75


def test_null_deal_value_is_empty_and_optional_fields_stay_empty(rig):
    add_cards(company_database_path("lity", rig.e.env), [{"id": 2, "title": "Bare"}])
    bare = by_title(admin_export(rig))["Bare"]
    for header in ("company", "contact", "contact_email", "contact_phone", "details", "deal_value_usd",
                   "probability_level", "next_action_date", "product_line", "lead_source", "salesperson_email"):
        assert bare[header] is None
    assert bare["deal_value_next_year_usd"] == 0  # the stored value is exported as is


def test_unresolved_salesperson_leaves_email_empty_and_stage_is_real_title(rig):
    add_cards(company_database_path("lity", rig.e.env),
              [{"id": 2, "title": "Won deal", "column_id": 2, "salesperson_id": 4242}],
              columns=[(2, "Award", "won")])
    row = by_title(admin_export(rig))["Won deal"]
    assert row["salesperson_email"] is None and row["stage"] == "Award"


def test_numbers_are_values_not_formulas(rig):
    add_cards(company_database_path("lity", rig.e.env), [{"id": 2, "title": "N", "deal_value": 10, "deal_value_next_year": 5}])
    ws = sheet(admin_export(rig))
    col = {c.value: c.column for c in ws[1]}
    row = next(r for r in ws.iter_rows(min_row=2) if r[1].value == "N")
    for header in ("deal_value_usd", "deal_value_next_year_usd"):
        cell = row[col[header] - 1]
        assert cell.data_type == "n", header


def test_no_bid_revenue_or_extra_columns(rig):
    data = admin_export(rig)
    assert table(data)[0] == HEADERS
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == [contract.SHEET_NAME, INSTRUCTIONS_SHEET, "Valid values"]  # the lists of this company and board
    assert not any(h.startswith(("revenue_", "bid_")) for h in HEADERS)


# --- formula safety -----------------------------------------------------------

@pytest.mark.parametrize("text", ["=1+1", "+SUM(A1)", "-2+3", "@cmd", '=HYPERLINK("http://x","y")'])
def test_dangerous_text_is_a_string_cell(rig, text):
    add_cards(company_database_path("lity", rig.e.env),
              [{"id": 2, "title": text, "company": text, "contact": text, "details": text}])
    ws = sheet(admin_export(rig))
    cells = [c for row in ws.iter_rows(min_row=2) for c in row if c.value == text]
    assert len(cells) == 4
    assert all(c.data_type == "s" and c.quotePrefix for c in cells)
    assert all(c.data_type != "f" for row in ws.iter_rows() for c in row)


def test_raw_xml_has_no_formula_element(rig):
    import zipfile
    add_cards(company_database_path("lity", rig.e.env), [{"id": 2, "title": "=1+1"}])
    z = zipfile.ZipFile(io.BytesIO(admin_export(rig)))
    for name in z.namelist():
        if name.startswith("xl/worksheets/"):
            assert b"<f>" not in z.read(name) and b"<f " not in z.read(name)


# --- authorization and isolation ------------------------------------------------

def test_company_data_never_crosses(rig):
    add_cards(company_database_path("lity", rig.e.env), [{"id": 2, "title": "lity-secret"}])
    titles_lity = set(by_title(admin_export(rig, "lity")))
    from database_targets import COMPANY_SLUGS
    for slug in COMPANY_SLUGS:
        if slug != "lity":
            assert "lity-secret" not in by_title(admin_export(rig, slug))
    assert "lity-secret" in titles_lity


def test_member_of_one_company_cannot_export_another(rig):
    from database_targets import COMPANY_SLUGS
    rig.e.member("m@example.com")
    token = rig.e.token("m@example.com")
    assert export(rig, "lity", token).status_code == 403  # sales_member: no Excel access in this version
    other = next(s for s in COMPANY_SLUGS if s != "lity")
    assert export(rig, other, token).status_code == 403
    assert template(rig, other, token).status_code == 403


def test_board_of_another_company_is_unreachable_by_id(rig):
    assert export(rig, "lity", rig.e.token("admin@example.com"), board=99).status_code == 404


def test_visitor_and_anonymous_are_refused_before_company_db_opens(rig):
    rig.e.member("v@example.com", role="visitor")
    for fetch in (export, template):
        assert fetch(rig, token=rig.e.token("v@example.com")).status_code == 403
        assert fetch(rig).status_code == 401
        assert fetch(rig, token="garbage").status_code == 401
    assert rig.opened == []


def test_member_may_not_download_either(rig):
    rig.e.member("m@example.com")
    token = rig.e.token("m@example.com")
    assert export(rig, "lity", token).status_code == 403 and template(rig, "lity", token).status_code == 403
    assert rig.opened == []


def test_response_headers_and_server_chosen_filename(rig):
    token = rig.e.token("admin@example.com")
    r = export(rig, "lity", token)
    assert r.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    assert r.headers["content-disposition"] == f'attachment; filename="cards-lity-board-1-{date.today():%Y%m%d}.xlsx"'
    assert r.headers["cache-control"] == "no-store"
    t = rig.client.get("/api/companies/lity/boards/1/excel/template?filename=../../evil.xlsx",
                       headers={"Authorization": f"Bearer {token}"})
    assert t.headers["content-disposition"] == 'attachment; filename="cards-import-template.xlsx"'
