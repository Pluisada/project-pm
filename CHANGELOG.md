# Changelog

User-facing changes, newest first. For design decisions, test results and known gaps behind each entry, see the matching part in `docs/PLAN.md`.

## 2026-10-13

### Undo (PLAN Part 15)
- Undo button in a bar pinned to the top of the board, labeled with what it will reverse (e.g. `Move "X" back to RFI`).
- Reverses your last card move, card or lead edit, card creation, or card deletion (admin). One level only.
- An AI assistant request counts as one change, so one click reverses all of it.
- Per user: you can only undo your own last change. Members can undo their own forward move, though they still can't drag cards backward.
- Refused, with no changes made, if the card was deleted since.

### Sales lead tracking and reports (PLAN Part 14)
- The board is now the Salcomp sales pipeline. The stages are 1st Contact, F2F Meeting, RFI, Proposal, Award (won) and Lost.
- Every card is a lead with these fields: company, contact, deal value + currency (EUR, USD, CNY, INR, BRL, VND), expected close date, lead source, product line (Chargers, Power supplies) and assigned salesperson (a registered user).
- Cards show a compact lead summary. Open close dates in the past are shown as overdue. "Edit lead" opens a form with all fields.
- Member edits to lead fields are logged in the card's "M" history, like title/details edits.
- The AI assistant knows the stages and lead fields, and can fill them in.
- New **Reports** screen (all users):
  - KPI tiles: open leads, open pipeline, won, lost, win rate.
  - Leads-by-stage chart.
  - Leads closing in the next 30 days or overdue.
  - Breakdowns by salesperson, product line and lead source.
  - Filters for salesperson and product line.
  - Totals are per currency, never converted.
- Existing databases get the new card columns automatically on startup. `backend/convert_to_sales_board.py` converts an existing board to the stages + sample leads.
- Fixed: dragging a card into the empty lower part of a short column snapped it back to another column.

### Forgot / reset password (PLAN Part 13)
- "Forgot my password?" on the login screen takes a username or email.
- It says whether the account exists. If it does, it emails a reset link that expires after 1 hour and works only once.
- Accounts without an email (the setup admin, admin-created members) are told to ask an admin.

### Email self sign-up (PLAN Part 12)
- "Sign up" on the login screen asks for an email and sends a link to set a password. New accounts are members.
- The email is the username. Login accepts a username or an email.
- Emails go out over SMTP, configured with `SMTP_*` in `.env` (Gmail with an app password works). Without SMTP, links are printed in the backend log.
- Security: signup and reset link tokens can never be used as login tokens.

## Earlier

- Card permissions by role and member edit history ("M" badge).
- Multi-user authentication: first-run admin setup, admin-only user management, shared board (PLAN Part 11).
- Kanban board, FastAPI backend, SQLite, AI chat sidebar, Docker packaging (PLAN Parts 1-10).
