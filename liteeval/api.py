"""Local-only JSON API. Keep the transport separate from reusable evaluation modules."""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .runner import compare
from .storage import NotFound, Store

MAX_BODY = 1_000_000


def dispatch(store: Store, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Testable route dispatcher. API is intended for loopback development only."""
    parsed = urlparse(path)
    route = parsed.path.rstrip("/")
    body = body or {}
    try:
        if method == "GET" and route == "/v1/health":
            return 200, {"status": "ok"}
        if method == "POST" and route == "/v1/projects":
            return 201, store.create_project(body["name"])
        if method == "POST" and route == "/v1/datasets":
            return 201, store.create_dataset(body["project_id"], body["name"])
        if method == "POST" and route == "/v1/traces":
            return 201, store.create_trace(body["project_id"], body["input"], body.get("output"),
                                           body.get("events"), body.get("status", "succeeded"),
                                           body.get("capture_level", "output_only"))
        if method == "GET" and re.fullmatch(r"/v1/traces/[0-9a-f]{32}", route):
            return 200, store.get_trace(route.rsplit("/", 1)[1])
        match = re.fullmatch(r"/v1/datasets/([0-9a-f]{32})/cases(/from-trace)?", route)
        if method == "POST" and match:
            if match.group(2):
                return 201, store.case_from_trace(match.group(1), body["trace_id"], body.get("expected"), body.get("metadata"))
            return 201, store.add_case(match.group(1), body["input"], body.get("expected"), body.get("metadata"))
        if method == "POST" and re.fullmatch(r"/v1/cases/[0-9a-f]{32}/revisions", route):
            return 201, store.revise_case(route.split("/")[3], body["input"], body.get("expected"), body.get("metadata"))
        if method == "POST" and route == "/v1/agent-versions":
            return 201, store.add_agent(body["project_id"], body["label"], body["config"])
        if method == "POST" and route == "/v1/evaluators":
            return 201, store.add_evaluator(body["project_id"], body["name"], body["kind"], body.get("config", {}))
        if method == "POST" and route == "/v1/experiments":
            return 201, store.create_experiment(body["project_id"], body["dataset_id"], body["agent_version_id"],
                                                body["evaluator_ids"])
        if method == "GET" and re.fullmatch(r"/v1/experiments/[0-9a-f]{32}", route):
            return 200, store.get_experiment(route.rsplit("/", 1)[1])
        if method == "GET" and re.fullmatch(r"/v1/runs/[0-9a-f]{32}", route):
            return 200, store.get_run(route.rsplit("/", 1)[1])
        if method == "GET" and route == "/v1/compare":
            query = parse_qs(parsed.query)
            return 200, compare(store, query["left"][0], query["right"][0])
        return 404, {"error": "not_found"}
    except NotFound as exc:
        return 404, {"error": "not_found", "message": str(exc)}
    except (KeyError, ValueError, TypeError) as exc:
        return 400, {"error": "invalid_request", "message": str(exc)}


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, method: str):
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                self.send_error(413)
                return
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                status, data = dispatch(store, method, self.path, body)
            except (ValueError, UnicodeDecodeError) as exc:
                status, data = 400, {"error": "invalid_json", "message": str(exc)}
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.respond("GET")

        def do_POST(self):
            self.respond("POST")

    return Handler


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Prototype API must bind to loopback")
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"LiteEval API listening at http://{host}:{port}", flush=True)
    httpd.serve_forever()
