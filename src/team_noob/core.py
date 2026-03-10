from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

NotifyHook = Callable[[dict[str, Any]], None]


class NotifyServer:
    """最简 HTTP 服务器，提供 /notify 与 /messages 接口。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        message_log_path: str | Path = "agent_messages.jsonl",
    ) -> None:
        self.host = host
        self.port = port
        self.message_log_path = Path(message_log_path)
        self._hooks: list[NotifyHook] = []

    def add_hook(self, hook: NotifyHook) -> None:
        """注册 notify 钩子。每次 /notify 被调用后都会执行。"""
        self._hooks.append(hook)

    def run(self) -> None:
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_latest_messages(self, limit: int) -> list[dict[str, Any]]:
                path = server.message_log_path
                if not path.exists():
                    return []
                lines = path.read_text(encoding="utf-8").splitlines()
                records: list[dict[str, Any]] = []
                for line in lines[-limit:]:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        records.append(item)
                return records

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/notify":
                    self._write_json(404, {"ok": False, "error": "not found"})
                    return

                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._write_json(400, {"ok": False, "error": "invalid json"})
                    return

                if not isinstance(payload, dict):
                    self._write_json(400, {"ok": False, "error": "json must be object"})
                    return

                try:
                    for hook in server._hooks:
                        hook(payload)
                except ValueError as exc:
                    self._write_json(400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:  # noqa: BLE001
                    self._write_json(500, {"ok": False, "error": f"hook failed: {exc}"})
                    return

                self._write_json(200, {"ok": True})

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/notify":
                    self._write_json(405, {"ok": False, "error": "use POST /notify"})
                    return
                if parsed.path == "/messages":
                    query = parse_qs(parsed.query)
                    raw_limit = query.get("limit", ["100"])[0]
                    try:
                        limit = int(raw_limit)
                    except ValueError:
                        self._write_json(400, {"ok": False, "error": "limit must be int"})
                        return
                    if limit <= 0:
                        self._write_json(400, {"ok": False, "error": "limit must be > 0"})
                        return
                    limit = min(limit, 1000)
                    messages = self._read_latest_messages(limit)
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "messages": messages,
                            "count": len(messages),
                            "limit": limit,
                        },
                    )
                    return
                self._write_json(404, {"ok": False, "error": "not found"})

            def log_message(self, format: str, *args: Any) -> None:
                # 保持最简输出，默认静默日志。
                return

        httpd = HTTPServer((self.host, self.port), _Handler)
        print(f"Notify server running at http://{self.host}:{self.port}")
        httpd.serve_forever()
