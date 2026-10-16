/**
 * Cleanup for the Import Bids specs, which share one E2E database with every
 * later spec (the dashboard, for one, asserts exact totals).
 *
 * Only cards this suite created are removed: those whose Bid code is one of
 * the fictitious E2E9xx codes from fixtures/make_bid_fixtures.py, plus any
 * ids a spec created by hand. Deletion goes through the admin DELETE API, so
 * the existing cascades and stage-event rules apply; nothing is written to the
 * database file directly. Import batches have no delete API and stay as audit
 * history (no other spec counts them).
 */
import { DatabaseSync } from "node:sqlite";
import { request as playwrightRequest } from "@playwright/test";
import { API_BASE_URL, E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME } from "./helpers";

export async function deleteBidSpecCards(dbPath: string, extraCardIds: number[] = []): Promise<void> {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  let cards: { id: number; board_id: number }[];
  try {
    cards = db
      .prepare(
        `SELECT c.id AS id, col.board_id AS board_id FROM card_source_refs r
         JOIN cards c ON c.id = r.card_id JOIN columns col ON col.id = c.column_id
         WHERE r.source_code LIKE 'E2E9%'`
      )
      .all() as { id: number; board_id: number }[];
  } finally {
    db.close();
  }

  const api = await playwrightRequest.newContext();
  try {
    const login = await api.post(`${API_BASE_URL}/api/login`, {
      data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
    const boards = await (await api.get(`${API_BASE_URL}/api/boards`, { headers })).json();
    const targets = [...cards, ...extraCardIds.map((id) => ({ id, board_id: boards[0].id as number }))];
    for (const { id, board_id } of targets) {
      // 404 = already gone (e.g. removed by the spec's own Undo).
      const response = await api.delete(`${API_BASE_URL}/api/boards/${board_id}/cards/${id}`, { headers });
      if (!response.ok() && response.status() !== 404) throw new Error(`cleanup of card ${id} failed: ${response.status()}`);
    }
  } finally {
    await api.dispose();
  }
}
