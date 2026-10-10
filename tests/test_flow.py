import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from liteeval.api import dispatch
from liteeval.runner import ExperimentRunner, compare
from liteeval.storage import Store


class Agent(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        question = body["input"]["question"]
        answer = "ok" if self.server.version == "good" else "wrong"
        payload = json.dumps({"answer": answer, "events": [{"kind": "tool", "name": "lookup"}]
                              if question == "tool" else []}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
        self.server.version = "bad"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_trace_to_case_experiments_and_comparison(self):
        status, project = dispatch(self.store, "POST", "/v1/projects", {"name": "Support"})
        self.assertEqual(status, 201)
        pid = project["id"]
        ds = self.store.create_dataset(pid, "Regression")
        production = self.store.create_trace(pid, {"question": "hello"}, "a failed answer", status="failed")
        case = self.store.case_from_trace(ds["id"], production["id"], "ok", {"severity": "high"})
        self.assertEqual(case["expected"], "ok")
        self.assertNotEqual(case["expected"], "a failed answer")
        tool_case = self.store.add_case(ds["id"], {"question": "tool"}, "ok")
        endpoint = f"http://127.0.0.1:{self.server.server_port}/agent"
        agent1 = self.store.add_agent(pid, "bad", {"type": "http", "endpoint": endpoint})
        agent2 = self.store.add_agent(pid, "good", {"type": "http", "endpoint": endpoint})
        exact = self.store.add_evaluator(pid, "exact", "exact_match", {})
        tool = self.store.add_evaluator(pid, "tool", "tool_called", {"tool_name": "lookup"})
        ids = [exact["id"], tool["id"]]
        e1 = self.store.create_experiment(pid, ds["id"], agent1["id"], ids)
        self.assertTrue(asyncio.run(ExperimentRunner(self.store).run(e1["id"])))
        self.assertFalse(asyncio.run(ExperimentRunner(self.store).run(e1["id"])))
        self.server.version = "good"
        e2 = self.store.create_experiment(pid, ds["id"], agent2["id"], ids)
        asyncio.run(ExperimentRunner(self.store).run(e2["id"]))
        result = compare(self.store, e1["id"], e2["id"])
        self.assertTrue(result["comparable"])
        self.assertEqual(result["counts"], {"improved": 2, "regressed": 0, "same": 1, "unscored": 1})
        runs = self.store.get_experiment(e1["id"])["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(self.store.get_run(runs[0]["id"])["trace"]["kind"], "eval")
        self.assertNotEqual(self.store.get_run(runs[0]["id"])["trace_id"], production["id"])
        revised = self.store.revise_case(case["id"], {"question": "hello"}, "new gold")
        self.assertEqual(revised["revision"], 2)
        e3 = self.store.create_experiment(pid, ds["id"], agent2["id"], ids)
        asyncio.run(ExperimentRunner(self.store).run(e3["id"]))
        self.assertFalse(compare(self.store, e1["id"], e3["id"])["comparable"])

    def test_project_isolation(self):
        p1 = self.store.create_project("One")["id"]
        p2 = self.store.create_project("Two")["id"]
        ds = self.store.create_dataset(p1, "Set")
        trace = self.store.create_trace(p2, {"question": "secret"})
        with self.assertRaises(ValueError):
            self.store.case_from_trace(ds["id"], trace["id"])
        self.store.add_case(ds["id"], {"question": "x"}, "x")
        agent = self.store.add_agent(p2, "v2", {"type": "http", "endpoint": "http://127.0.0.1/"})
        evaluator = self.store.add_evaluator(p1, "exact", "exact_match", {})
        with self.assertRaises(ValueError):
            self.store.create_experiment(p1, ds["id"], agent["id"], [evaluator["id"]])


if __name__ == "__main__":
    unittest.main()
