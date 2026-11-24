import json
import socket
import threading

import probe


def test_escape_probes_fail_when_network_is_down(monkeypatch):
    def refuse(*_a, **_k):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(-3, "fail")))
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    results = {pid: probe.run_probe(kind, target)[0] for pid, _, kind, target in probe.ESCAPE_PROBES}
    assert not any(results.values()), results


def test_control_probe_detects_reachable_service(capsys):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    probe.main(["--json", "--expect-reachable", f"127.0.0.1:{port}",
                *sum((["--skip", p[0]] for p in probe.ESCAPE_PROBES), [])])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["results"][0]["type"] == "control" and out["results"][0]["passed"]
    server.close()


def test_control_probe_fails_when_service_missing(capsys):
    rc = probe.main(["--json", "--expect-reachable", "127.0.0.1:1",
                     *sum((["--skip", p[0]] for p in probe.ESCAPE_PROBES), [])])
    assert rc == 1
