"""One-off: convert an existing board to the Salcomp sales pipeline.

Deletes the first board's columns (their cards cascade with them), then
recreates the sales stages with sample leads. Users are untouched. Back up
pm.db first:

    cp pm.db pm.db.bak && uv run python convert_to_sales_board.py
"""
from database import SessionLocal, init_db, seed_pipeline
from models import Board, User


def main() -> None:
    init_db()  # refuses a missing or un-migrated database; it repairs nothing
    db = SessionLocal()
    try:
        board = db.query(Board).order_by(Board.id).first()
        if board is None:
            print("No board yet - run first-run setup instead.")
            return
        admin = db.query(User).filter(User.role == "admin").order_by(User.id).first()
        for column in list(board.columns):
            db.delete(column)
        board.title = "Salcomp Sales Pipeline"
        board.description = "Shared sales lead board"
        db.flush()
        seed_pipeline(db, board, admin)
        print(f"Board {board.id} converted: {len(board.columns)} stages.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
