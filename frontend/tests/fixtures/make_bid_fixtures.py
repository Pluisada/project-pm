"""Writes the synthetic Bids workbooks the Import Bids E2E spec uploads.

Run from backend/ (it reuses the test workbook builder, so the headers always
match the reader):  uv run python ../frontend/tests/fixtures/make_bid_fixtures.py <out_dir>

All data is fictitious. Output is deterministic: the zip members get a fixed
timestamp and the workbook properties a fixed date, so the same script always
yields the same bytes.
"""
import io
import sys
import zipfile
from pathlib import Path

from test_bid_excel_reader import build, row

FIXED_TIME = (2026, 1, 1, 0, 0, 0)

CODE = {"alpha": "E2E900-01", "bravo": "E2E901-01", "charlie": "E2E902-01", "delta": "E2E903-01", "echo": "E2E904-01", "foxtrot": "E2E905-01", "golf": "E2E906-01"}
CODE.update({"hotel": "E2E907-01", "india": "E2E908-01", "juliet": "E2E909-01", "kilo": "E2E910-01", "lima": "E2E911-01", "mike": "E2E912-01", "november": "E2E913-01", "oscar": "E2E914-01", "papa": "E2E915-01"})


def lead(key: str, project: str, **overrides):
    return row(**{
        "Código": CODE[key],
        "Cliente": f"Cliente E2E {key.title()}",
        "Nome do projeto": project,
        **overrides,
    })


# Initial import: one row per kind of outcome.
INITIAL = [
    lead("alpha", "E2E Alpha"),                                                                    # row 2 -> Proposal
    lead("bravo", "E2E Bravo", CAT="WON", **{"Estágio": "AWARD GIVEN"}),                           # row 3 -> Award
    lead("charlie", "E2E Charlie", CAT="CLOSED", **{"Estágio": "NOT AWARDED BY CUSTOMER"}),        # row 4 -> confirm Lost
    lead("delta", "E2E Delta", CAT="CLOSED", **{"Estágio": "CLOSED (BY SALCOMP)"}),                # row 5 -> choose a stage
]

# Same Alpha bid, newer and renamed: an update that needs confirmation.
UPDATE = [lead("alpha", "E2E Alpha Renamed", **{"Atualizado em": "01/04/2026"})]

# A row with an invalid date: a row error, the row is kept but flagged.
BAD_ROW = [lead("alpha", "E2E Broken", **{"Data de MP": "not-a-date"})]


# A brand-new bid, for the conflict scenarios.
FRESH = [lead("echo", "E2E Echo")]
REPLAY = [lead("foxtrot", "E2E Foxtrot")]

# Undo scenarios: a new bid, then the same bid newer and renamed.
UNDO_NEW = [lead("golf", "E2E Golf")]
UNDO_UPDATE = [lead("golf", "E2E Golf From Import", **{"Atualizado em": "01/04/2026"})]

# Revert scenarios (bid-import.revert.spec.ts): each test owns its bids, so the batches never overlap by accident.
REVERT_NEW = [lead("hotel", "E2E Hotel"), lead("india", "E2E India")]
REVERT_EDIT = [lead("juliet", "E2E Juliet")]
REVERT_DEP_A = [lead("kilo", "E2E Kilo")]
REVERT_DEP_B = [lead("kilo", "E2E Kilo Renamed", **{"Atualizado em": "01/04/2026"})]
REVERT_OBSERVED = [lead("lima", "E2E Lima")]
# Same Lima bid, newer and renamed: an update that stays unconfirmed, so its batch only observes the card.
REVERT_OBSERVED_UPDATE = [lead("lima", "E2E Lima Renamed", **{"Atualizado em": "01/04/2026"})]
REVERT_DOUBLE = [lead("mike", "E2E Mike")]
REVERT_STALE = [lead("november", "E2E November")]
REVERT_NET_LOST = [lead("oscar", "E2E Oscar")]
REVERT_NET_AMBIGUOUS = [lead("papa", "E2E Papa")]


def deterministic(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            clone = zipfile.ZipInfo(info.filename, FIXED_TIME)
            clone.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(clone, src.read(info.filename))
    return out.getvalue()


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"bids-initial.xlsx": INITIAL, "bids-update.xlsx": UPDATE, "bids-bad-row.xlsx": BAD_ROW, "bids-fresh.xlsx": FRESH, "bids-replay.xlsx": REPLAY, "bids-undo-new.xlsx": UNDO_NEW, "bids-undo-update.xlsx": UNDO_UPDATE,
             "revert-new.xlsx": REVERT_NEW, "revert-edit.xlsx": REVERT_EDIT, "revert-dep-a.xlsx": REVERT_DEP_A, "revert-dep-b.xlsx": REVERT_DEP_B,
             "revert-observed.xlsx": REVERT_OBSERVED, "revert-observed-update.xlsx": REVERT_OBSERVED_UPDATE, "revert-double.xlsx": REVERT_DOUBLE, "revert-stale.xlsx": REVERT_STALE,
             "revert-net-lost.xlsx": REVERT_NET_LOST, "revert-net-ambiguous.xlsx": REVERT_NET_AMBIGUOUS}
    for name, rows in files.items():
        (out_dir / name).write_bytes(deterministic(build(rows)))
    (out_dir / "not-a-workbook.xlsx").write_bytes(b"this is plain text, not a workbook\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
