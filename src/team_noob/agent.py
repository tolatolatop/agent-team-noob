from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing as mp
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

from .core import NotifyServer

DEFAULT_PIPELINE = "default"
MESSAGE_LOG_FILE = Path("agent_messages.jsonl")
STATE_FILE = Path("agent_state.json")
SHUTDOWN_KIND = "shutdown"

stream_handler = logging.StreamHandler()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(stream_handler)

Message = dict[str, Any]
Envelope = dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_content_from_notify(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def normalize_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    data_attr = getattr(message, "data", None)
    if isinstance(data_attr, dict):
        return data_attr
    for attr in ("model_dump", "dict"):
        fn = getattr(message, attr, None)
        if callable(fn):
            data = fn()
            if isinstance(data, dict):
                return data
    return {"raw": repr(message)}


def extract_session_id(message: dict[str, Any]) -> str | None:
    keys = ("session_id", "sessionId")
    for key in keys:
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    for nested_key in ("message", "data"):
        nested = message.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def load_options(path: Path) -> ClaudeAgentOptions:
    with path.open("r", encoding="utf-8") as f:
        return ClaudeAgentOptions.from_json(f.read())


def build_default_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        allowed_tools=["Read", "Grep"],
        permission_mode="acceptEdits",
        hooks={"PreToolUse": [HookMatcher(matcher=".*", hooks=[tool_hook])]},
    )


async def tool_hook(input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
    logger.info("tool_hook: %s, %s, %s", input_data, tool_use_id, context)
    return {}


def with_session_id(options: ClaudeAgentOptions, session_id: str) -> ClaudeAgentOptions:
    if hasattr(options, "model_copy"):
        return options.model_copy(update={"session_id": session_id})
    setattr(options, "session_id", session_id)
    return options


async def maybe_compact_once(client: ClaudeSDKClient) -> None:
    compact_fn = getattr(client, "compact", None)
    if callable(compact_fn):
        result = compact_fn()
        if asyncio.iscoroutine(result):
            await result
        logger.info("compact finished once at startup (client.compact)")
        return

    # Fallback: use slash command to ensure startup compact is executed once.
    compact_stream = build_single_message_stream("/compact")
    compact_task = asyncio.create_task(client.query(compact_stream))
    async for _ in client.receive_response():
        pass
    await compact_task
    logger.info("compact finished once at startup (slash command)")


def build_single_message_stream(content: str) -> AsyncGenerator[Message, None]:
    async def _stream() -> AsyncGenerator[Message, None]:
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
        }

    return _stream()


async def run_agent_query_receive(
    client: ClaudeSDKClient,
    content: str,
    message_log_path: Path,
    state: dict[str, Any],
) -> None:
    stream_input = build_single_message_stream(content)
    append_jsonl(
        message_log_path,
        {
            "ts": utc_now_iso(),
            "type": "user",
            "pipeline": DEFAULT_PIPELINE,
            "content": content,
            "session_id": state.get("session_id"),
        },
    )
    query_task = asyncio.create_task(client.query(stream_input))
    async for response in client.receive_response():
        response_data = normalize_message(response)
        maybe_session_id = extract_session_id(response_data)
        if maybe_session_id:
            state["session_id"] = maybe_session_id
        append_jsonl(
            message_log_path,
            {
                "ts": utc_now_iso(),
                "type": "assistant",
                "pipeline": DEFAULT_PIPELINE,
                "message": response_data,
                "session_id": state.get("session_id"),
            },
        )
    await query_task


async def worker_loop(
    notify_queue: mp.Queue,
    message_log_path: Path,
    state_path: Path,
    options: ClaudeAgentOptions,
) -> None:
    state = load_state(state_path)
    existing_session = state.get("session_id")
    if isinstance(existing_session, str) and existing_session:
        options = with_session_id(options, existing_session)
        logger.info("loaded existing session_id=%s", existing_session)

    async with ClaudeSDKClient(options) as client:
        if isinstance(existing_session, str) and existing_session:
            await maybe_compact_once(client)
        while True:
            envelope = await asyncio.to_thread(notify_queue.get)
            if not isinstance(envelope, dict):
                logger.warning("invalid envelope=%r", envelope)
                continue
            kind = envelope.get("kind")
            if kind == SHUTDOWN_KIND:
                logger.info("worker received shutdown signal")
                break
            if kind != "notify":
                logger.warning("unknown kind=%r", kind)
                continue
            payload = envelope.get("payload")
            content = read_content_from_notify(payload) if isinstance(payload, dict) else None
            if not content:
                logger.warning("skip notify payload without content")
                continue
            try:
                await run_agent_query_receive(client, content, message_log_path, state)
                state["updated_at"] = utc_now_iso()
                save_state(state_path, state)
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker failed processing notify: %s", exc)
                append_jsonl(
                    message_log_path,
                    {
                        "ts": utc_now_iso(),
                        "type": "error",
                        "pipeline": DEFAULT_PIPELINE,
                        "error": str(exc),
                    },
                )


def claude_worker_main(
    notify_queue: mp.Queue,
    message_log_path: str,
    state_path: str,
) -> None:
    options = build_default_options()
    asyncio.run(
        worker_loop(
            notify_queue=notify_queue,
            message_log_path=Path(message_log_path),
            state_path=Path(state_path),
            options=options,
        )
    )


def create_notify_hook(notify_queue: mp.Queue):
    def _hook(payload: dict[str, Any]) -> None:
        pipeline = payload.get("pipeline", DEFAULT_PIPELINE)
        if pipeline != DEFAULT_PIPELINE:
            raise ValueError(f"unsupported pipeline: {pipeline}")
        notify_queue.put(
            {
                "kind": "notify",
                "pipeline": DEFAULT_PIPELINE,
                "payload": payload,
            }
        )

    return _hook


def run_service(host: str = "127.0.0.1", port: int = 8000) -> None:
    notify_queue: mp.Queue = mp.Queue()
    message_log_path = MESSAGE_LOG_FILE.resolve()
    state_path = STATE_FILE.resolve()
    worker = mp.Process(
        target=claude_worker_main,
        args=(notify_queue, str(message_log_path), str(state_path)),
        name="claude_worker_default",
        daemon=True,
    )
    worker.start()
    logger.info(
        "service start pid=%s worker=%s pipeline=%s",
        os.getpid(),
        worker.pid,
        DEFAULT_PIPELINE,
    )
    server = NotifyServer(host=host, port=port)
    server.add_hook(create_notify_hook(notify_queue))
    try:
        server.run()
    finally:
        notify_queue.put({"kind": SHUTDOWN_KIND})
        worker.join(timeout=10)
        if worker.is_alive():
            logger.warning("worker still alive, terminate now")
            worker.terminate()
            worker.join(timeout=5)


def main() -> None:
    run_service()


if __name__ == "__main__":
    main()
