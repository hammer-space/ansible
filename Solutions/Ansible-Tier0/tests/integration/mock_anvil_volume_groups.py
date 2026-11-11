#!/usr/bin/env python3
"""Minimal stand-in for the Anvil volume-group endpoints.

Used by test_volume_group_create.yml. Behaviour is chosen by the GROUP NAME so
one server covers every case:

    name contains "reject"  POST -> 400, body echoes the Authorization header
                            as a JSON object (the shape the real Anvil uses
                            in 4002 bodies — field report 2026-11-02)
    name contains "never"   POST -> 202 with no Location; never listed
    anything else           POST -> 202 + Location; task COMPLETED; listed
                            with a uuid afterwards

What this does NOT prove: that a real Anvil accepts the POST body. The
location entry shape matches the one the join PUTs successfully in the field;
the create call itself is unverified against a live cluster.

Usage: mock_anvil_volume_groups.py PORT
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

PREFIX = "/mgmt/v1.2/rest"
groups = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, obj=None, headers=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith(PREFIX + "/tasks/"):
            return self.reply(200, {"status": "COMPLETED"})
        if self.path.startswith(PREFIX + "/volume-groups/"):
            name = unquote(self.path[len(PREFIX + "/volume-groups/"):])
            if name in groups:
                return self.reply(200, groups[name])
            return self.reply(404, {"errorCode": 4002})
        return self.reply(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        name = body.get("name", "")
        if "reject" in name:
            return self.reply(400, {
                "errorCode": 4002,
                "headers": {"authorization": self.headers.get("Authorization")},
            })
        if "never" in name:
            return self.reply(202)
        groups[name] = dict(body, uoid={"uuid": "uuid-" + name})
        location = "http://127.0.0.1:%d%s/tasks/t-%s" % (self.server.server_port, PREFIX, name)
        return self.reply(202, None, {"Location": location})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
