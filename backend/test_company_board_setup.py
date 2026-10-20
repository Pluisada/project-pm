"""B4: a Super Admin prepares an empty company (a board plus the default stages) through the existing admin API, and
the board then serves the Excel template and accepts an import by stage title. Disposable tmp_path databases only.
No initializer creates a board, so nothing is duplicated and no board id is assumed."""
import io

import pytest
from openpyxl import load_workbook

from database_targets import COMPANY_SLUGS
from excel_cards_writer import build_workbook
from lead_options import PIPELINE_STAGES
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def api(rig, method, slug, path, token, **kw):
    return rig.client.request(method, f"/api/companies/{slug}{path}", headers={"Authorization": f"Bearer {token}"}, **kw)


def empty_company(rig, slug):
    """A company as the initializers leave it: schema and catalog, no board, no column, no card."""
    for table in ("card_edit_history", "audit_events", "card_stage_events", "cards", "columns", "boards"):
        rig.sql(f"DELETE FROM {table}", slug=slug)


def build_default_board(rig, slug, token):
    board = api(rig, "post", slug, "/boards", token, json={"title": "Sales Pipeline"})
    assert board.status_code == 201
    board_id = board.json()["id"]
    for index, (title, kind) in enumerate(PIPELINE_STAGES):
        r = api(rig, "post", slug, f"/boards/{board_id}/columns", token, json={"title": title, "position": index, "stage_kind": kind})
        assert r.status_code == 201
    return board_id


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_super_admin_prepares_each_company_and_the_template_and_import_use_that_board(rig, slug):
    token = admin(rig)
    empty_company(rig, slug)
    assert api(rig, "get", slug, "/boards", token).json() == []  # nothing is created by the initializers
    board_id = build_default_board(rig, slug, token)
    columns = api(rig, "get", slug, f"/boards/{board_id}", token).json()["columns"]
    assert [(c["title"], c["stage_kind"]) for c in columns] == PIPELINE_STAGES

    template = api(rig, "get", slug, f"/boards/{board_id}/excel/template", token)
    assert template.status_code == 200 and load_workbook(io.BytesIO(template.content)).sheetnames

    content = build_workbook([{"title": "First lead", "stage": "Proposal"}, {"title": "Won lead", "stage": "Award"}])
    files = {"file": ("cards.xlsx", content, XLSX)}
    preview = api(rig, "post", slug, f"/boards/{board_id}/excel/import/preview", token, files=files).json()
    assert preview["confirmable"] is True, preview
    done = api(rig, "post", slug, f"/boards/{board_id}/excel/import/confirm", token, files=files,
               data={"confirm": "true", "plan_digest": preview["plan_digest"]})
    assert done.status_code == 201
    stages = rig.sql("SELECT c.title, k.title FROM cards c JOIN columns k ON k.id = c.column_id ORDER BY c.id", slug=slug)
    assert stages == [("First lead", "Proposal"), ("Won lead", "Award")]


def test_a_stage_title_that_is_not_on_the_board_is_still_refused(rig):
    token = admin(rig)
    empty_company(rig, "lity")
    board_id = build_default_board(rig, "lity", token)
    files = {"file": ("cards.xlsx", build_workbook([{"title": "x", "stage": "Qualification"}]), XLSX)}
    preview = api(rig, "post", "lity", f"/boards/{board_id}/excel/import/preview", token, files=files).json()
    assert preview["confirmable"] is False


def test_board_administration_is_super_admin_and_company_isolated(rig):
    token = admin(rig)
    empty_company(rig, "salcomp")
    empty_company(rig, "lity")
    board_id = build_default_board(rig, "salcomp", token)
    for role in ("sales_member", "visitor"):
        _, other = member(rig, f"{role}@example.com", role=role, slug="salcomp")
        assert api(rig, "post", "salcomp", "/boards", other, json={"title": "x"}).status_code == 403
        assert api(rig, "post", "salcomp", f"/boards/{board_id}/columns", other, json={"title": "x"}).status_code == 403
    _, lity_member = member(rig, "l@example.com", slug="lity")
    assert api(rig, "get", "salcomp", f"/boards/{board_id}", lity_member).status_code == 403  # no membership there
    assert api(rig, "get", "lity", f"/boards/{board_id}", token).status_code == 404  # the other company has no such board
    assert api(rig, "get", "lity", "/boards", token).json() == []
