"""Company product catalog: the products an opportunity's `product_line` may name.

The catalog is the `products` table of one company database; it lives on its own
declarative base so it is neither part of the required company schema contract
(company_schema.REQUIRED_COMPANY_SCHEMA, so company databases created before the
catalog keep validating) nor copied into the legacy single-database metadata.
Every function takes the session of the already authorized company and nothing is
cached, so a catalog can never be read or written through another company's session.

A database without the table is "not initialized": reads fall back to the built-in
defaults (lead_options.PRODUCT_LINES) and writes are refused until
init_company_products.py has created the table. That is also the behaviour of the
legacy single-company database, which has no catalog.

cards.product_line stores the product name. A product still referenced by an
opportunity can be neither renamed nor deleted, so references never dangle.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, func, inspect, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session

from lead_options import PRODUCT_LINES
from models import Card

ProductBase = declarative_base()

NAME_MAX_LENGTH = 100


class Product(ProductBase):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String(NAME_MAX_LENGTH), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ProductError(Exception):
    """A refused catalog operation; `status` is the HTTP status the route answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def has_catalog(db: Session) -> bool:
    return inspect(db.connection()).has_table(Product.__tablename__)  # the session's own connection: no side transaction


def catalog_names(db: Session) -> list[str]:
    """The product names this database allows, in creation order (the defaults when not initialized)."""
    if not has_catalog(db):
        return list(PRODUCT_LINES)
    return list(db.scalars(select(Product.name).order_by(Product.id)))


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise ProductError(422, f"Product name must be 1-{NAME_MAX_LENGTH} characters")
    return name


def _require_catalog(db: Session) -> None:
    if not has_catalog(db):
        raise ProductError(409, "The product catalog of this company is not initialized")


def _require_free_name(db: Session, name: str, own_id: int | None = None) -> None:
    query = select(Product.id).where(func.lower(Product.name) == name.lower())
    if own_id is not None:
        query = query.where(Product.id != own_id)
    if db.scalar(query) is not None:
        raise ProductError(409, "A product with this name already exists")


def _get(db: Session, product_id: int) -> Product:
    _require_catalog(db)
    product = db.get(Product, product_id)
    if product is None:
        raise ProductError(404, "Product not found")
    return product


def card_count(db: Session, name: str) -> int:
    return db.scalar(select(func.count(Card.id)).where(Card.product_line == name)) or 0


def list_products(db: Session) -> list[dict]:
    """[{id, name, card_count}]. Uninitialized: the defaults with id None."""
    if not has_catalog(db):
        return [{"id": None, "name": n, "card_count": card_count(db, n)} for n in PRODUCT_LINES]
    return [
        {"id": p.id, "name": p.name, "card_count": card_count(db, p.name)}
        for p in db.scalars(select(Product).order_by(Product.id))
    ]


def create_product(db: Session, name: str) -> Product:
    _require_catalog(db)
    name = _clean_name(name)
    _require_free_name(db, name)
    now = _now()
    product = Product(name=name, created_at=now, updated_at=now)
    db.add(product)
    db.flush()
    return product


def rename_product(db: Session, product_id: int, name: str) -> Product:
    product = _get(db, product_id)
    name = _clean_name(name)
    if name == product.name:
        return product
    _require_free_name(db, name, product.id)
    if card_count(db, product.name):
        raise ProductError(409, "Opportunities use this product; it cannot be renamed")
    product.name = name
    product.updated_at = _now()
    db.flush()
    return product


def delete_product(db: Session, product_id: int) -> None:
    product = _get(db, product_id)
    if card_count(db, product.name):
        raise ProductError(409, "Opportunities use this product; it cannot be deleted")
    db.delete(product)
    db.flush()


def initialize_catalog(db: Session) -> list[str]:
    """Create the table if missing and add any default product not present (case-insensitive).

    Idempotent; never removes, renames or overwrites a product. Returns the names added.
    The caller owns the transaction.
    """
    ProductBase.metadata.create_all(db.connection())
    existing = {n.lower() for n in db.scalars(select(Product.name))}
    added = []
    for name in PRODUCT_LINES:
        if name.lower() not in existing:
            now = _now()
            db.add(Product(name=name, created_at=now, updated_at=now))
            added.append(name)
    db.flush()
    return added


def check_product_line(db: Session, fields: dict, current: str | None = None) -> None:
    """Raise ProductError(422) when `fields` sets a product_line this database's catalog does not allow.

    A value equal to the card's current one is accepted, so an unrelated edit never fails on
    a legacy reference that is not in the catalog; clearing (None) is always accepted.
    """
    value = fields.get("product_line")
    if value is None or value == current:
        return
    if value not in catalog_names(db):
        raise ProductError(422, "product_line: not a product of this company's catalog")
