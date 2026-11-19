#!/usr/bin/env python3
"""Stand-in Anvil for test_volume_group_delete.yml.

Serves enough of /nodes, /storage-volumes, /volume-groups and /tasks for
decommission_tier0.yml and cleanup_instance_nodes.py to run end to end.

State at start:
    nodes    hb-1 (10.1.16.5), hb-2 (10.1.16.6)
    volumes  hb-1::/nvme/tier0, hb-2::/nvme/tier0
    groups   tier0-118_a      hb-1 only               -> emptied by hb-1
             keep-me-118      hb-1 only               -> emptied, name filter
             vg-shared        hb-1 + hb-2 + an older hb-1 placeholder
                                                      -> still in use
             vg-pattern       namePattern '*'         -> selects by pattern
             tier0-118_old    hb-1 + "__z_volume:" ref to an older deleted
                              volume                  -> still in use
             tier0-reject     hb-1 only; DELETE -> 400 echoing the
                              Authorization header as a JSON object
             tier0-sticky     hb-1 only; DELETE -> 202, never goes away

Deleting a storage volume replaces it, in every group, with a reference to a
hidden "__z_volume:<volume name>" volume group, as a real Anvil does.
DELETE /volume-groups/<x> is accepted only when <x> is the
group's uuid: by NAME it is rejected, like a PUT by name on a real Anvil.

Modes (argv[3], optional):
    fail-node-delete   DELETE /nodes/<uuid> -> 500
    join-on-delete     deleting a volume adds a foreign volume to tier0-118_a,
                       i.e. another job joined it between plan and delete
    slow-delete        a deleted volume / node stays LISTED for the next 3
                       reads of /storage-volumes / /nodes (async delete)
    stuck-volume       a deleted volume never leaves the list; it sits in
                       DECOMMISSIONING_QUIESCED (field: its node had gone)
In slow-delete and stuck-volume, GET /storage-volumes/<name> answers 404, as a
field Anvil does for a name containing "/", so cleanup_instance_nodes.py
believes the volume is gone at once — the playbook's wait must not.

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


def placeholder(name, taken=()):
    # The hidden group name must be unique: when "__z_volume:<name>" is
    # already used, the Anvil appends _<n> (field: "...::/nvme/tier0_14").
    zname, n = "__z_volume:" + name, 0
    while zname in taken:
        n += 1
        zname = "__z_volume:%s_%d" % (name, n)
    return {"_type": "VOLUME_GROUP",
            "uoid": {"uuid": "z-" + zname, "objectType": "VOLUME_GROUP"},
            "created": 1795085323975, "modified": 1795085323975, "extendedInfo": {},
            "name": zname, "internalId": 1342177398}


def placeholder_names():
    return {l.get("name") for g in groups.values() for x in g["expressions"]
            for l in x.get("locations", []) if l.get("_type") == "VOLUME_GROUP"}


def group(name, expressions):
    return {"_type": "VOLUME_GROUP", "name": name, "uoid": {"uuid": "uuid-" + name},
            "expressions": expressions}


groups = {g["name"]: g for g in [
    group("tier0-118_a", [{"operator": "IN", "locations": [loc(V1)]}]),
    group("keep-me-118", [{"operator": "IN", "locations": [loc(V1)]}]),
    # also holds the placeholder of an EARLIER volume named like hb-1's, so
    # deleting hb-1 now yields "__z_volume:hb-1::/nvme/tier0_1"
    group("vg-shared", [{"operator": "IN",
                         "locations": [loc(V1), loc(V2), placeholder(V1)]}]),
    group("vg-pattern", [{"expressionType": "VOLUME_LOCATION", "namePattern": "*"}]),
    # holds hb-1 AND a placeholder left by an older, already deleted volume
    group("tier0-118_old", [{"operator": "IN",
                             "locations": [loc(V1), placeholder("hb-0::/nvme/tier0")]}]),
    group("tier0-reject", [{"operator": "IN", "locations": [loc(V1)]}]),
    group("tier0-sticky", [{"operator": "IN", "locations": [loc(V1)]}]),
]}
calls = []
pending_volumes = {}   # name -> list reads left before it disappears
pending_nodes = {}
SLOW_READS = 3


def finish_volume_delete(name):
    volumes.pop(name, None)
    taken = placeholder_names()
    for g in groups.values():
        for x in g["expressions"]:
            if "locations" in x:
                x["locations"] = [placeholder(name, taken)
                                  if l.get("storageVolume", {}).get("name") == name else l
                                  for l in x["locations"]]


def tick(pending, finish):
    for name in list(pending):
        pending[name] -= 1
        if pending[name] <= 0:
            del pending[name]
            finish(name)


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
            out = list(nodes.values())
            tick(pending_nodes, lambda n: nodes.pop(n, None))
            return self.reply(200, out)
        if p == "storage-volumes":
            out = list(volumes.values())
            tick(pending_volumes, finish_volume_delete)
            return self.reply(200, out)
        if p.startswith("storage-volumes/"):
            if MODE in ("slow-delete", "stuck-volume"):
                return self.reply(404, {"errorCode": 4002})
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
            if name not in volumes:
                return self.reply(404, {"errorCode": 4002})
            # A real Anvil does not drop the deleted volume from its groups: it
            # swaps the location for a hidden per-volume group reference
            # (finish_volume_delete). Shape captured from a field Anvil.
            if MODE == "stuck-volume":
                volumes[name]["storageVolumeState"] = "DECOMMISSIONING_QUIESCED"
                volumes[name]["operationalStatus"] = "UNAVAILABLE"
            elif MODE == "slow-delete":
                volumes[name]["storageVolumeState"] = "DECOMMISSIONING"
                pending_volumes[name] = SLOW_READS
            else:
                finish_volume_delete(name)
            if MODE == "join-on-delete":
                groups["tier0-118_a"]["expressions"][0]["locations"].append(loc("hb-9::/nvme/tier0"))
            return self.reply(200, {})
        if p.startswith("nodes/"):
            if MODE == "fail-node-delete":
                return self.reply(500, {"errorCode": 5000})
            uuid = p[len("nodes/"):]
            for n in list(nodes):
                if nodes[n]["uoid"]["uuid"] == uuid:
                    if MODE == "slow-delete":
                        pending_nodes[n] = SLOW_READS
                    else:
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
