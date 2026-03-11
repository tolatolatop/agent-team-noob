from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MESSAGES_LIMIT = 100


@dataclass
class CliState:
    base_url: str = DEFAULT_BASE_URL


def _request_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = Request(url=url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def post_notify(base_url: str, text: str) -> dict[str, Any]:
    payload = {"pipeline": "default", "message": {"content": text}}
    return _request_json("POST", f"{base_url}/notify", payload)


def get_messages(base_url: str, limit: int = DEFAULT_MESSAGES_LIMIT) -> dict[str, Any]:
    query = urlencode({"limit": limit})
    return _request_json("GET", f"{base_url}/messages?{query}")


def _short_payload(payload: Any, max_len: int = 120) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def handle_command(state: CliState, line: str) -> str:
    parts = line.strip().split()
    if not parts:
        return ""
    cmd = parts[0]

    if cmd == "/connect":
        if len(parts) != 2:
            return "usage: /connect <base_url>"
        state.base_url = parts[1].rstrip("/")
        return f"connected: {state.base_url}"

    if cmd == "/notify":
        text = line[len("/notify") :].strip()
        if not text:
            return "usage: /notify <text>"
        try:
            data = post_notify(state.base_url, text)
            return json.dumps(data, ensure_ascii=False)
        except HTTPError as exc:
            return f"http error: {exc.code}"
        except URLError as exc:
            return f"network error: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    if cmd == "/messages":
        if len(parts) > 2:
            return "usage: /messages [limit]"
        limit = DEFAULT_MESSAGES_LIMIT
        if len(parts) == 2:
            try:
                limit = int(parts[1])
            except ValueError:
                return "usage: /messages [limit]"
            if limit <= 0:
                return "usage: /messages [limit]"
        try:
            data = get_messages(state.base_url, limit=limit)
            if not isinstance(data, dict):
                return str(data)
            messages = data.get("messages", [])
            header = f"count={data.get('count', 0)} limit={data.get('limit', limit)}"
            rows = [header]
            for item in messages:
                if not isinstance(item, dict):
                    rows.append(_short_payload(item))
                    continue
                rows.append(
                    f"{item.get('type', '-')}\t{item.get('session_id', '-')}\t{_short_payload(item.get('payload'))}"
                )
            return "\n".join(rows)
        except HTTPError as exc:
            return f"http error: {exc.code}"
        except URLError as exc:
            return f"network error: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return "unknown command, use: /connect /notify /messages"


def run_cli() -> None:
    state = CliState()
    print(f"cli ready, base_url={state.base_url}")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break
        if not line:
            continue
        if line in {"/exit", "exit", "quit"}:
            break
        print(handle_command(state, line))


def main() -> None:
    run_cli()


if __name__ == "__main__":
    main()
