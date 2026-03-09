from __future__ import annotations

import http.client
import json
import multiprocessing as mp
import socket
import time
from typing import Any

from team_noob.core import NotifyServer


def _find_free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run_server(port: int, mode: str) -> None:
    server = NotifyServer(host="127.0.0.1", port=port)
    if mode == "value_error":
        server.add_hook(lambda _payload: (_ for _ in ()).throw(ValueError("bad pipeline")))
    elif mode == "runtime_error":
        server.add_hook(lambda _payload: (_ for _ in ()).throw(RuntimeError("hook boom")))
    server.run()


def _wait_server_ready(port: int, timeout_s: float = 3.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            conn.request("GET", "/")
            conn.getresponse().read()
            return
        except OSError:
            time.sleep(0.05)
        finally:
            conn.close()
    raise AssertionError("server did not become ready in time")


def _request_json(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        raw = b""
        headers: dict[str, str] = {}
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw if raw else None, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        return resp.status, payload
    finally:
        conn.close()


def _request_invalid_json(port: int) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request(
            "POST",
            "/notify",
            body=b"{invalid",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        return resp.status, payload
    finally:
        conn.close()


def test_notify_server_invalid_json_and_get_method() -> None:
    port = _find_free_port()
    proc = mp.Process(target=_run_server, args=(port, "ok"), daemon=True)
    proc.start()
    try:
        _wait_server_ready(port)
        status_get, payload_get = _request_json(port, "GET", "/notify")
        assert status_get == 405
        assert payload_get["ok"] is False

        status_json, payload_json = _request_invalid_json(port)
        assert status_json == 400
        assert payload_json["error"] == "invalid json"
    finally:
        proc.terminate()
        proc.join(timeout=2)


def test_notify_server_maps_value_error_to_400() -> None:
    port = _find_free_port()
    proc = mp.Process(target=_run_server, args=(port, "value_error"), daemon=True)
    proc.start()
    try:
        _wait_server_ready(port)
        status, payload = _request_json(port, "POST", "/notify", {"pipeline": "default"})
        assert status == 400
        assert payload["ok"] is False
        assert "bad pipeline" in payload["error"]
    finally:
        proc.terminate()
        proc.join(timeout=2)


def test_notify_server_maps_generic_error_to_500() -> None:
    port = _find_free_port()
    proc = mp.Process(target=_run_server, args=(port, "runtime_error"), daemon=True)
    proc.start()
    try:
        _wait_server_ready(port)
        status, payload = _request_json(port, "POST", "/notify", {"pipeline": "default"})
        assert status == 500
        assert payload["ok"] is False
        assert "hook failed" in payload["error"]
    finally:
        proc.terminate()
        proc.join(timeout=2)
