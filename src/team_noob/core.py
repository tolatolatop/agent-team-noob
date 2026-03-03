from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

NotifyHook = Callable[[dict[str, Any]], None]


class NotifyServer:
    """最简 HTTP 服务器，仅提供 /notify 接口。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.host = host
        self.port = port
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

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/notify":
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

                for hook in server._hooks:
                    hook(payload)

                self._write_json(200, {"ok": True})

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/notify":
                    self._write_json(405, {"ok": False, "error": "use POST /notify"})
                    return
                self._write_json(404, {"ok": False, "error": "not found"})

            def log_message(self, format: str, *args: Any) -> None:
                # 保持最简输出，默认静默日志。
                return

        httpd = HTTPServer((self.host, self.port), _Handler)
        print(f"Notify server running at http://{self.host}:{self.port}")
        httpd.serve_forever()
