from __future__ import annotations

from team_noob import cli


def test_connect_command_updates_base_url() -> None:
    state = cli.CliState()
    out = cli.handle_command(state, "/connect http://127.0.0.1:18080")
    assert out == "connected: http://127.0.0.1:18080"
    assert state.base_url == "http://127.0.0.1:18080"


def test_notify_command_requires_text() -> None:
    state = cli.CliState()
    out = cli.handle_command(state, "/notify")
    assert out == "usage: /notify <text>"


def test_messages_default_limit_is_100(monkeypatch) -> None:
    state = cli.CliState()
    captured: dict[str, int] = {}

    def fake_get_messages(base_url: str, limit: int = 100):
        captured["limit"] = limit
        return {"ok": True, "count": 0, "limit": limit, "messages": []}

    monkeypatch.setattr(cli, "get_messages", fake_get_messages)
    out = cli.handle_command(state, "/messages")
    assert "count=0 limit=100" in out
    assert captured["limit"] == 100


def test_messages_with_limit(monkeypatch) -> None:
    state = cli.CliState()

    def fake_get_messages(base_url: str, limit: int = 100):
        return {
            "ok": True,
            "count": 1,
            "limit": limit,
            "messages": [{"type": "query", "session_id": "s1", "payload": {"content": "x"}}],
        }

    monkeypatch.setattr(cli, "get_messages", fake_get_messages)
    out = cli.handle_command(state, "/messages 5")
    assert "count=1 limit=5" in out
    assert "query\ts1" in out


def test_messages_usage_on_invalid_limit() -> None:
    state = cli.CliState()
    assert cli.handle_command(state, "/messages 0") == "usage: /messages [limit]"
    assert cli.handle_command(state, "/messages bad") == "usage: /messages [limit]"
