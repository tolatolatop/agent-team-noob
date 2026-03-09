from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from team_noob import agent


class DummyQueue:
    def __init__(self, items: list[Any] | None = None) -> None:
        self.items = list(items or [])

    def put(self, item: Any) -> None:
        self.items.append(item)

    def get(self) -> Any:
        if not self.items:
            raise RuntimeError("queue is empty")
        return self.items.pop(0)


class DummyOptions:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id

    def model_copy(self, update: dict[str, Any]) -> "DummyOptions":
        return DummyOptions(session_id=update.get("session_id", self.session_id))


class FakeClaudeClient:
    response_batches: list[list[dict[str, Any]]] = []
    instances: list["FakeClaudeClient"] = []

    def __init__(self, options: Any) -> None:
        self.options = options
        self.query_contents: list[str] = []
        FakeClaudeClient.instances.append(self)

    async def __aenter__(self) -> "FakeClaudeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def query(self, stream_input) -> None:
        async for event in stream_input:
            message = event.get("message", {})
            self.query_contents.append(message.get("content", ""))

    async def receive_response(self):
        batch = FakeClaudeClient.response_batches.pop(0)
        for item in batch:
            yield item


def test_create_notify_hook_default_pipeline_enqueues() -> None:
    q = DummyQueue()
    hook = agent.create_notify_hook(q)
    payload = {"pipeline": "default", "message": {"content": "hello"}}

    hook(payload)

    assert len(q.items) == 1
    assert q.items[0]["kind"] == "notify"
    assert q.items[0]["pipeline"] == "default"
    assert q.items[0]["payload"]["message"]["content"] == "hello"


def test_create_notify_hook_non_default_rejected() -> None:
    q = DummyQueue()
    hook = agent.create_notify_hook(q)

    try:
        hook({"pipeline": "other", "message": {"content": "hello"}})
        assert False, "expected ValueError for non-default pipeline"
    except ValueError as exc:
        assert "unsupported pipeline" in str(exc)


def test_read_content_from_notify() -> None:
    assert agent.read_content_from_notify({"message": {"content": " x "}}) == "x"
    assert agent.read_content_from_notify({"content": " y "}) == "y"
    assert agent.read_content_from_notify({"message": {"content": "   "}}) is None
    assert agent.read_content_from_notify({"foo": "bar"}) is None
    assert agent.read_content_from_notify("bad payload") is None


def test_worker_loop_persists_messages_and_session(tmp_path: Path, monkeypatch) -> None:
    message_log = tmp_path / "agent_messages.jsonl"
    state_file = tmp_path / "agent_state.json"
    queue = DummyQueue(
        [
            {"kind": "notify", "payload": {"pipeline": "default", "content": "hello"}},
            {"kind": "shutdown"},
        ]
    )

    FakeClaudeClient.instances = []
    FakeClaudeClient.response_batches = [[{"type": "system", "session_id": "sess-1"}]]
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeClient)

    asyncio.run(
        agent.worker_loop(
            notify_queue=queue,
            message_log_path=message_log,
            state_path=state_file,
            options=DummyOptions(),
        )
    )

    lines = message_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["type"] == "user"
    assert second["type"] == "assistant"
    assert second["session_id"] == "sess-1"

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["session_id"] == "sess-1"
    assert "updated_at" in state


def test_worker_loop_restart_loads_session_and_compacts_once(
    tmp_path: Path, monkeypatch
) -> None:
    message_log = tmp_path / "agent_messages.jsonl"
    state_file = tmp_path / "agent_state.json"
    state_file.write_text(json.dumps({"session_id": "sess-old"}), encoding="utf-8")
    queue = DummyQueue(
        [
            {"kind": "notify", "payload": {"pipeline": "default", "content": "hello2"}},
            {"kind": "shutdown"},
        ]
    )

    FakeClaudeClient.instances = []
    FakeClaudeClient.response_batches = [
        [],
        [{"type": "system", "session_id": "sess-new"}],
    ]
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeClient)

    asyncio.run(
        agent.worker_loop(
            notify_queue=queue,
            message_log_path=message_log,
            state_path=state_file,
            options=DummyOptions(),
        )
    )

    instance = FakeClaudeClient.instances[0]
    assert isinstance(instance.options, DummyOptions)
    assert instance.options.session_id == "sess-old"
    assert instance.query_contents.count("/compact") == 1
    assert "hello2" in instance.query_contents

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["session_id"] == "sess-new"
