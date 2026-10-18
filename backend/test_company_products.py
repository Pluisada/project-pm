"""Company product catalogs: per-company storage, admin-only writes, card references.

Disposable tmp_path identity and company databases with synthetic data only.
"""
import pytest

from company_runtime import CompanyRuntime
from database_targets import COMPANY_SLUGS, company_database_path
from init_company_products import init_company_products
from product_catalog import catalog_names
from test_company_card_writes import NOW, Rig, admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)

DEFAULTS = ["Chargers", "Power supplies"]


def products(rig, token, slug="lity"):
    return rig.client.get(f"/api/companies/{slug}/products", headers={"Authorization": f"Bearer {token}"})


def send(rig, method, path, token, slug="lity", **kw):
    return rig.client.request(method, f"/api/companies/{slug}{path}", headers={"Authorization": f"Bearer {token}"}, **kw)


def names(rig, token, slug="lity"):
    return [p["name"] for p in products(rig, token, slug).json()["products"]]


def test_new_company_database_starts_with_the_defaults(rig):
    for slug in COMPANY_SLUGS:
        body = products(rig, admin(rig), slug).json()
        assert body["initialized"] is True
        assert [p["name"] for p in body["products"]] == DEFAULTS


def test_admin_creates_renames_and_deletes(rig):
    token = admin(rig)
    r = send(rig, "post", "/products", token, json={"name": "  Adapters "})
    assert r.status_code == 201 and r.json()["name"] == "Adapters"
    pid = r.json()["id"]
    assert names(rig, token) == DEFAULTS + ["Adapters"]
    r = send(rig, "put", f"/products/{pid}", token, json={"name": "Cables"})
    assert r.status_code == 200 and r.json()["name"] == "Cables"
    assert send(rig, "delete", f"/products/{pid}", token).status_code == 204
    assert names(rig, token) == DEFAULTS
    assert send(rig, "delete", f"/products/{pid}", token).status_code == 404


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_names_are_refused(rig, name):
    assert send(rig, "post", "/products", admin(rig), json={"name": name}).status_code == 422


def test_duplicate_names_are_refused_case_insensitively(rig):
    token = admin(rig)
    assert send(rig, "post", "/products", token, json={"name": "chargers"}).status_code == 409
    assert send(rig, "put", "/products/2", token, json={"name": "CHARGERS"}).status_code == 409
    assert names(rig, token) == DEFAULTS


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_non_admins_read_but_cannot_write(rig, role):
    _, token = member(rig, role=role)
    assert names(rig, token) == DEFAULTS
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in (
        ("post", "/products", {"json": {"name": "X"}}),
        ("put", "/products/1", {"json": {"name": "X"}}),
        ("delete", "/products/1", {}),
    ):
        assert send(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == []  # refused before the company database was opened
    assert rig.digest("lity") == before


def test_unauthenticated_requests_are_refused(rig):
    for method, path in (("get", "/products"), ("post", "/products"), ("delete", "/products/1")):
        r = rig.client.request(method, f"/api/companies/lity{path}", json={"name": "X"} if method == "post" else None)
        assert r.status_code == 401
    assert rig.opened == []


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_a_product_created_in_one_company_exists_only_there(rig, slug):
    token = admin(rig)
    assert send(rig, "post", "/products", token, slug=slug, json={"name": f"Only {slug}"}).status_code == 201
    for other in COMPANY_SLUGS:
        expected = DEFAULTS + [f"Only {slug}"] if other == slug else DEFAULTS
        assert names(rig, token, other) == expected
        assert rig.sql("SELECT name FROM products ORDER BY id", slug=other) == [(n,) for n in expected]


def test_member_of_one_company_cannot_touch_the_catalog_of_another(rig):
    rig.e.member("lmember@example.com", slug="lity")
    token = rig.e.token("lmember@example.com")
    before = {s: rig.digest(s) for s in COMPANY_SLUGS}
    for method, path, kw in (
        ("post", "/products", {"json": {"name": "X"}}),
        ("put", "/products/1", {"json": {"name": "X"}}),
        ("delete", "/products/1", {}),
        ("get", "/products", {}),
    ):
        for other in ("salcomp", "valor-log"):
            assert send(rig, method, path, token, slug=other, **kw).status_code == 403
    assert {s: rig.digest(s) for s in COMPANY_SLUGS} == before


def test_a_record_id_of_another_company_resolves_to_nothing(rig):
    token = admin(rig)
    pid = send(rig, "post", "/products", token, json={"name": "Only lity"}).json()["id"]
    assert send(rig, "put", f"/products/{pid}", token, slug="salcomp", json={"name": "Hijack"}).status_code == 404
    assert send(rig, "delete", f"/products/{pid}", token, slug="valor-log").status_code == 404
    assert names(rig, token, "salcomp") == DEFAULTS


def test_missing_company_configuration_fails_safely_without_database_url(rig, tmp_path):
    fallback = tmp_path / "fallback.db"
    env = {k: v for k, v in rig.e.env.items() if k != "PM_COMPANY_DB_LITY"}
    env["DATABASE_URL"] = f"sqlite:///{fallback}"
    rig.runtime.dispose()
    rig.runtime._environ = env
    r = products(rig, admin(rig))
    assert r.status_code == 503
    assert send(rig, "post", "/products", admin(rig), json={"name": "X"}).status_code == 503
    assert not fallback.exists()


def put_card(rig, token, card_id, **fields):
    return rig.call("put", f"/cards/{card_id}", token, json=fields)


def test_card_product_must_be_in_this_companys_catalog(rig):
    token = admin(rig)
    assert put_card(rig, token, 1, product_line="Chargers").status_code == 200
    assert put_card(rig, token, 1, product_line="Adapters").status_code == 422
    send(rig, "post", "/products", token, json={"name": "Adapters"})
    assert put_card(rig, token, 1, product_line="Adapters").json()["product_line"] == "Adapters"
    # The same name is not valid in a company that does not have it.
    assert rig.call("put", "/cards/1", token, slug="salcomp", json={"product_line": "Adapters"}).status_code == 422
    assert put_card(rig, token, 1, product_line=None).json()["product_line"] is None


def test_product_in_use_cannot_be_renamed_or_deleted(rig):
    token = admin(rig)
    put_card(rig, token, 1, product_line="Chargers")
    assert send(rig, "put", "/products/1", token, json={"name": "Wall chargers"}).status_code == 409
    assert send(rig, "delete", "/products/1", token).status_code == 409
    assert names(rig, token) == DEFAULTS
    assert {p["name"]: p["card_count"] for p in products(rig, token).json()["products"]} == {"Chargers": 1, "Power supplies": 0}
    assert rig.sql("SELECT product_line FROM cards WHERE id = 1") == [("Chargers",)]
    put_card(rig, token, 1, product_line=None)
    assert send(rig, "put", "/products/1", token, json={"name": "Wall chargers"}).status_code == 200


def test_legacy_reference_outside_the_catalog_is_kept_not_replaced(rig):
    token = admin(rig)
    rig.sql("UPDATE cards SET product_line = 'Batteries' WHERE id = 1")
    r = put_card(rig, token, 1, title="renamed")  # unrelated edit succeeds
    assert r.status_code == 200 and r.json()["product_line"] == "Batteries"
    assert put_card(rig, token, 1, product_line="Batteries").status_code == 200  # unchanged value accepted
    assert put_card(rig, token, 2, product_line="Batteries").status_code == 422  # but never newly chosen
    assert [(p["name"], p["card_count"]) for p in products(rig, token).json()["products"]] == [("Chargers", 0), ("Power supplies", 0)]


def test_member_can_choose_only_catalog_products_on_their_own_card(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert put_card(rig, token, 1, product_line="Power supplies").status_code == 200
    assert put_card(rig, token, 1, product_line="Nope").status_code == 422


def test_lead_options_follow_the_company_catalog(rig):
    token = admin(rig)
    send(rig, "post", "/products", token, json={"name": "Adapters"})
    opts = lambda slug: send(rig, "get", "/lead-options", token, slug=slug).json()["product_lines"]
    assert opts("lity") == DEFAULTS + ["Adapters"]
    assert opts("salcomp") == DEFAULTS and opts("valor-log") == DEFAULTS


def test_empty_catalog(rig):
    token = admin(rig)
    for pid in (1, 2):
        assert send(rig, "delete", f"/products/{pid}", token).status_code == 204
    body = products(rig, token).json()
    assert body == {"initialized": True, "products": []}
    assert send(rig, "get", "/lead-options", token).json()["product_lines"] == []
    assert put_card(rig, token, 1, product_line="Chargers").status_code == 422
    assert put_card(rig, token, 1, title="still editable").status_code == 200


def test_company_without_catalog_table_falls_back_to_defaults_and_refuses_writes(rig):
    token = admin(rig)
    rig.sql("DROP TABLE products")
    body = products(rig, token).json()
    assert body["initialized"] is False
    assert [(p["id"], p["name"]) for p in body["products"]] == [(None, "Chargers"), (None, "Power supplies")]
    assert send(rig, "get", "/lead-options", token).json()["product_lines"] == DEFAULTS
    assert put_card(rig, token, 1, product_line="Chargers").status_code == 200
    for method, path, kw in (("post", "/products", {"json": {"name": "X"}}), ("put", "/products/1", {"json": {"name": "X"}}),
                             ("delete", "/products/1", {})):
        assert send(rig, method, path, token, **kw).status_code == 409
    assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'products'") == []  # reads and refusals created nothing


def test_initialization_is_per_company_idempotent_and_non_destructive(rig):
    token = admin(rig)
    env = rig.e.env
    rig.runtime.dispose()
    for slug in ("lity", "salcomp"):
        rig.sql("DROP TABLE products", slug=slug)
    rig.sql("CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)", slug="lity")
    rig.sql("INSERT INTO products (name, created_at, updated_at) VALUES ('chargers', ?, ?), ('Adapters', ?, ?)",
            (NOW, NOW, NOW, NOW), slug="lity")
    before = {s: rig.digest(s) for s in COMPANY_SLUGS}

    assert init_company_products("lity", False, env) == ["Power supplies"]  # dry run: lowercase 'chargers' counts as present
    assert init_company_products("salcomp", False, env) == DEFAULTS
    assert {s: rig.digest(s) for s in COMPANY_SLUGS} == before

    assert init_company_products("lity", True, env) == ["Power supplies"]
    assert init_company_products("lity", True, env) == []  # idempotent
    assert rig.sql("SELECT name FROM products ORDER BY id", slug="lity") == [("chargers",), ("Adapters",), ("Power supplies",)]
    assert {s: rig.digest(s) for s in COMPANY_SLUGS if s != "lity"} == {s: d for s, d in before.items() if s != "lity"}
    assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'products'", slug="salcomp") == []  # untouched

    assert init_company_products("salcomp", True, env) == DEFAULTS
    assert rig.sql("SELECT name FROM products ORDER BY id", slug="salcomp") == [(n,) for n in DEFAULTS]
    assert rig.sql("SELECT name FROM products ORDER BY id", slug="valor-log") == [(n,) for n in DEFAULTS]


def test_initialization_needs_an_explicit_existing_company_database(tmp_path):
    from database_targets import DatabaseTargetError
    with pytest.raises(DatabaseTargetError):
        init_company_products("lity", True, {"DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}"})
    assert not (tmp_path / "x.db").exists()
