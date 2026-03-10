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
        if isinstance(stream_input, str):
            self.query_contents.append(stream_input)
            return
        async for event in stream_input:
            message = event.get("message", {})
            self.query_contents.append(message.get("content", ""))

    async def receive_response(self):
        batch = FakeClaudeClient.response_batches.pop(0)
        for item in batch:
            yield item


class FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeThinkingBlock:
    def __init__(self, thinking: str, signature: str = "sig") -> None:
        self.thinking = thinking
        self.signature = signature


class FakeToolUseBlock:
    def __init__(self, block_id: str, name: str, input_data: dict[str, Any]) -> None:
        self.id = block_id
        self.name = name
        self.input = input_data


class FakeToolResultBlock:
    def __init__(self, tool_use_id: str, content: Any, is_error: bool = False) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class FakeAssistantMessage:
    def __init__(self, content: list[Any], model: str = "test-model") -> None:
        self.content = content
        self.model = model


class FakeSystemMessage:
    def __init__(self, subtype: str, data: dict[str, Any]) -> None:
        self.subtype = subtype
        self.data = data


class FakeResultMessage:
    def __init__(self, session_id: str, result: str, is_error: bool = False) -> None:
        self.session_id = session_id
        self.result = result
        self.is_error = is_error
        self.subtype = "success"


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
    FakeClaudeClient.response_batches = [
        [
            FakeSystemMessage("init", {"session_id": "sess-1"}),
            FakeAssistantMessage(
                [
                    FakeThinkingBlock("reasoning"),
                    FakeToolUseBlock("tool-1", "Read", {"file_path": "a.txt"}),
                    FakeToolResultBlock("tool-1", "ok"),
                    FakeTextBlock("final answer"),
                ]
            ),
            FakeResultMessage("sess-1", "done"),
        ]
    ]
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeClient)
    monkeypatch.setattr(agent, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(agent, "SystemMessage", FakeSystemMessage)
    monkeypatch.setattr(agent, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(agent, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(agent, "ThinkingBlock", FakeThinkingBlock)
    monkeypatch.setattr(agent, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(agent, "ToolResultBlock", FakeToolResultBlock)

    asyncio.run(
        agent.worker_loop(
            notify_queue=queue,
            message_log_path=message_log,
            state_path=state_file,
            options=DummyOptions(),
        )
    )

    lines = message_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 5
    first = json.loads(lines[0])
    assert first["type"] == "query"
    event_types = [json.loads(line)["type"] for line in lines]
    assert "ai_message" in event_types
    assert "thinking_message" in event_types
    assert "tool_call_message" in event_types
    assert "tool_result_message" in event_types

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
        [FakeSystemMessage("init", {"session_id": "sess-new"})],
    ]
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeClient)
    monkeypatch.setattr(agent, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(agent, "SystemMessage", FakeSystemMessage)
    monkeypatch.setattr(agent, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(agent, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(agent, "ThinkingBlock", FakeThinkingBlock)
    monkeypatch.setattr(agent, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(agent, "ToolResultBlock", FakeToolResultBlock)

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


def test_build_hooks_writes_pre_post_failure(tmp_path: Path) -> None:
    message_log = tmp_path / "agent_messages.jsonl"
    hooks = agent.build_hooks(message_log)
    context = type("Ctx", (), {"session_id": "sess-hook", "cwd": "/tmp"})()

    asyncio.run(
        hooks["PreToolUse"][0].hooks[0](
            {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}},
            "tool-1",
            context,
        )
    )
    asyncio.run(
        hooks["PostToolUse"][0].hooks[0](
            {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_response": "ok"},
            "tool-1",
            context,
        )
    )
    asyncio.run(
        hooks["PostToolUseFailure"][0].hooks[0](
            {
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Read",
                "error": "boom",
            },
            "tool-1",
            context,
        )
    )

    lines = [json.loads(line) for line in message_log.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "tool_call_message"
    assert lines[1]["type"] == "tool_result_message"
    assert lines[2]["type"] == "tool_result_message"
    assert lines[0]["session_id"] == "sess-hook"
