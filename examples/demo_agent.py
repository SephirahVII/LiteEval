"""Tiny replaceable HTTP Agent: python examples/demo_agent.py [v1|v2] [port]."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = sys.argv[1] if len(sys.argv) > 1 else "v1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8766


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        question = request["input"]["question"]
        answer = "退款请在订单页申请" if question == "如何退款？" else ("请联系人工客服" if VERSION == "v2" else "不知道")
        payload = json.dumps({"answer": answer}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


print(f"Demo agent {VERSION} on port {PORT}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
