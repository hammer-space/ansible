#!/usr/bin/env python3
"""Stand-in Anvil for test_volume_group_delete.yml.

Serves enough of /nodes, /storage-volumes, /volume-groups and /tasks for
decommission_tier0.yml and cleanup_instance_nodes.py to run end to end.

State at start:
    nodes    hb-1 (10.1.16.5), hb-2 (10.1.16.6)
    volumes  hb-1::/nvme/tier0, hb-2::/nvme/tier0
    groups   tier0-118_a      hb-1 only               -> emptied by hb-1
             keep-me-118      hb-1 only               -> emptied, name filter
             vg-shared        hb-1 + hb-2             -> still in use
             vg-pattern       namePattern '*'         -> selects by pattern
             tier0-reject     hb-1 only; DELETE -> 400 echoing the
                              Authorization header as a JSON object
             tier0-sticky     hb-1 only; DELETE -> 202, never goes away

Deleting a storage volume removes it from every group (the cascade the real
Anvil performs). DELETE /volume-groups/<x> is accepted only when <x> is the
group's uuid: by NAME it is rejected, like a PUT by name on a real Anvil.

Modes (argv[3], optional):
    fail-node-delete   DELETE /nodes/<uuid> -> 500
    join-on-delete     deleting a volume adds a foreign volume to tier0-118_a,
                       i.e. another job joined it between plan and delete

GET /_calls returns every write as "METHOD path", for the assertions.

Usage: mock_anvil_decommission.py PORT [CERTFILE:KEYFILE|-] [MODE]
"""
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

PREFIX = "/mgmt/v1.2/rest"
MODE = sys.argv[3] if len(sys.argv) > 3 else ""

V1, V2 = "hb-1::/nvme/tier0", "hb-2::/nvme/tier0"
nodes = {
    "hb-1": {"name": "hb-1", "uoid": {"uuid": "node-1"}, "mgmtIpAddress": {"address": "10.1.16.5"}},
    "hb-2": {"name": "hb-2", "uoid": {"uuid": "node-2"}, "mgmtIpAddress": {"address": "10.1.16.6"}},
}
volumes = {V1: {"name": V1, "node": {"name": "hb-1"}}, V2: {"name": V2, "node": {"name": "hb-2"}}}


def loc(name):
    return {"_type": "VOLUME_LOCATION", "storageVolume": {"_type": "STORAGE_VOLUME", "name": name}}


def group(name, expressions):
    return {"_type": "VOLUME_GROUP", "name": name, "uoid": {"uuid": "uuid-" + name},
            "expressions": expressions}


groups = {g["name"]: g for g in [
    group("tier0-118_a", [{"operator": "IN", "locations": [loc(V1)]}]),
    group("keep-me-118", [{"operator": "IN", "locations": [loc(V1)]}]),
    group("vg-shared", [{"operator": "IN", "locations": [loc(V1), loc(V2)]}]),
    group("vg-pattern", [{"expressionType": "VOLUME_LOCATION", "namePattern": "*"}]),
    group("tier0-reject", [{"operator": "IN", "locations": [loc(V1)]}]),
    group("tier0-sticky", [{"operator": "IN", "locations": [loc(V1)]}]),
]}
calls = []


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

    def rest(self):
        return unquote(self.path[len(PREFIX) + 1:]) if self.path.startswith(PREFIX + "/") else None

    def task(self, name):
        scheme = "https" if isinstance(self.server.socket, ssl.SSLSocket) else "http"
        return {"Location": "%s://127.0.0.1:%d%s/tasks/%s" % (scheme, self.server.server_port, PREFIX, name)}

    def do_GET(self):
        if self.path == "/_calls":
            return self.reply(200, calls)
        p = self.rest()
        if p is None:
            return self.reply(404, {})
        if p.startswith("tasks/"):
            return self.reply(200, {"status": "COMPLETED"})
        if p == "nodes":
            return self.reply(200, list(nodes.values()))
        if p == "storage-volumes":
            return self.reply(200, list(volumes.values()))
        if p.startswith("storage-volumes/"):
            v = volumes.get(p[len("storage-volumes/"):])
            return self.reply(200, v) if v else self.reply(404, {"errorCode": 4002})
        if p == "volume-groups":
            return self.reply(200, list(groups.values()))
        if p.startswith("volume-groups/"):
            g = groups.get(p[len("volume-groups/"):])
            return self.reply(200, g) if g else self.reply(404, {"errorCode": 4002})
        return self.reply(404, {})

    def do_DELETE(self):
        p = self.rest() or ""
        calls.append("DELETE " + p)
        if p.startswith("storage-volumes/"):
            name = p[len("storage-volumes/"):]
            if volumes.pop(name, None) is None:
                return self.reply(404, {"errorCode": 4002})
            for g in groups.values():
                for x in g["expressions"]:
                    if "locations" in x:
                        x["locations"] = [l for l in x["locations"]
                                          if l.get("storageVolume", {}).get("name") != name]
            if MODE == "join-on-delete":
                groups["tier0-118_a"]["expressions"][0]["locations"].append(loc("hb-9::/nvme/tier0"))
            return self.reply(200, {})
        if p.startswith("nodes/"):
            if MODE == "fail-node-delete":
                return self.reply(500, {"errorCode": 5000})
            uuid = p[len("nodes/"):]
            for n in list(nodes):
                if nodes[n]["uoid"]["uuid"] == uuid:
                    del nodes[n]
            return self.reply(200, {})
        if p.startswith("volume-groups/"):
            ref = p[len("volume-groups/"):]
            match = [g for g in groups.values() if g["uoid"]["uuid"] == ref]
            if not match:
                code = 400 if ref in groups else 404
                return self.reply(code, {"errorCode": 4000, "message": "address by uuid"})
            name = match[0]["name"]
            if "reject" in name:
                return self.reply(400, {"errorCode": 4002,
                                        "headers": {"authorization": self.headers.get("Authorization")}})
            if "sticky" not in name:
                del groups[name]
            return self.reply(202, None, self.task("del-" + name))
        return self.reply(404, {})


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    tls = sys.argv[2] if len(sys.argv) > 2 else "-"
    if tls != "-":
        cert, key = tls.split(":")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
