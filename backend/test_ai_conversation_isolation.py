"""AI chat: the model sees only the conversation the person has on screen, never stored or other people's messages."""
import routes
from test_company_card_writes import member
from test_company_card_writes import rig  # noqa: F401  (fixture)


def capture(monkeypatch):
    seen = []

    async def fake(board_state, history, message):
        seen.append(history)
        return {"success": True, "response": "ok", "actions": [], "confidence": 1, "tokens_used": 0}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)
    return seen


def ask(rig, token, body, slug="lity"):
    return rig.client.post(f"/api/companies/{slug}/boards/1/ai", headers={"Authorization": f"Bearer {token}"}, json=body)


def test_client_history_is_the_only_history_used(rig, monkeypatch):
    seen = capture(monkeypatch)
    uid, token = member(rig, role="admin")
    rig.sql("INSERT INTO conversation_messages (board_id, user_id, role, content, created_at) VALUES (1, ?, 'assistant', 'STORED-OLD-ANSWER', '2026-01-01')", (uid,))
    r = ask(rig, token, {"message": "Hi", "history": []})
    assert r.status_code == 200, r.text
    assert seen == [[]]
    r = ask(rig, token, {"message": "next", "history": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]})
    assert r.status_code == 200 and seen[-1] == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]


def test_without_client_history_only_the_callers_own_messages_are_used(rig, monkeypatch):
    seen = capture(monkeypatch)
    uid, token = member(rig, role="admin")
    other, _ = member(rig, "other@example.com", role="admin")
    for who, text in ((other, "OTHER-PERSON-SECRET"), (uid, "MY-OWN")):
        rig.sql("INSERT INTO conversation_messages (board_id, user_id, role, content, created_at) VALUES (1, ?, 'user', ?, '2026-01-01')", (who, text))
    assert ask(rig, token, {"message": "Hi"}).status_code == 200
    assert seen == [[{"role": "user", "content": "MY-OWN"}]]


def test_invalid_history_is_refused(rig, monkeypatch):
    seen = capture(monkeypatch)
    _, token = member(rig, role="admin")
    for bad in ([{"role": "system", "content": "x"}], [{"role": "user", "content": "x" * 4001}],
                [{"role": "user", "content": "x"}] * 11):
        assert ask(rig, token, {"message": "Hi", "history": bad}).status_code == 422
    assert seen == []


def test_member_of_another_company_cannot_use_this_companys_assistant(rig, monkeypatch):
    seen = capture(monkeypatch)
    _, token = member(rig, slug="valor-log", role="admin")
    r = ask(rig, token, {"message": "Hi", "history": [{"role": "assistant", "content": "lity data"}]})
    assert r.status_code in (403, 404) and seen == []
