"""Testes do aquecimento do gateway (gateway/bootstrap_keys.py), sem Docker.

O aquecimento é uma otimização da subida: nenhuma falha nele pode impedir a gravação do arquivo
de pronto, que o healthcheck do gateway exige. Cada teste troca o endereço do gateway por um
servidor local de mentira e confere que `warm_up()` volta sem exceção, e rápido.

Execução: `python -m pytest tests/gateway` (só precisa do pytest).
"""

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "gateway" / "bootstrap_keys.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bk(monkeypatch):
    """O módulo do bootstrap, com o gateway apontando para uma porta sem ninguém e prazos curtos."""
    monkeypatch.setenv("LITELLM_MASTER_KEY", "chave-de-teste")
    monkeypatch.setitem(sys.modules, "yaml", sys.modules.get("yaml") or types.ModuleType("yaml"))
    spec = importlib.util.spec_from_file_location("bootstrap_keys_em_teste", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE = f"http://127.0.0.1:{free_port()}"
    module.WARMUP_PORT = free_port()
    module.CALL_TIMEOUT = 1
    return module


def serve(handler) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_warm_up(bk, limit: float = 3.0) -> float:
    """Roda warm_up() numa thread e falha se ele levantar exceção ou demorar mais que `limit`."""
    errors, start = [], time.perf_counter()

    def target():
        try:
            bk.warm_up()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(limit)
    assert not worker.is_alive(), f"warm_up() não voltou em {limit} s"
    assert not errors, f"warm_up() levantou {errors[0]!r}"
    return time.perf_counter() - start


def test_happy_path_goes_through_the_local_stub_once(bk):
    """O 'gateway' de mentira faz o que o LiteLLM faz: chama o destino gateway-warmup (o servidor
    local do bootstrap) e devolve a resposta dele."""
    forwarded = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            stub = f"http://127.0.0.1:{bk.WARMUP_PORT}/v1/chat/completions"
            import urllib.request
            with urllib.request.urlopen(urllib.request.Request(stub, data=b"{}", method="POST")) as r:
                body = r.read()
            forwarded.append(json.loads(body))
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    gateway = serve(Gateway)
    bk.BASE = f"http://127.0.0.1:{gateway.server_address[1]}"
    run_warm_up(bk)
    gateway.shutdown()
    assert len(forwarded) == 1
    assert forwarded[0]["choices"][0]["message"]["content"] == "ok"
    with socket.socket() as s:  # o servidor local foi desligado e liberou a porta
        s.bind(("127.0.0.1", bk.WARMUP_PORT))


def test_local_port_already_in_use(bk):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", bk.WARMUP_PORT))
        busy.listen()
        run_warm_up(bk)


def test_gateway_unreachable(bk):
    run_warm_up(bk)


def test_gateway_accepts_but_never_answers(bk):
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        bk.BASE = f"http://127.0.0.1:{silent.getsockname()[1]}"
        assert run_warm_up(bk) < bk.CALL_TIMEOUT + 2


def test_gateway_answers_200_with_a_body_that_is_not_json(bk):
    class NotJson(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.send_header("Content-Length", "4"); self.end_headers()
            self.wfile.write(b"oops")

        def log_message(self, *args):
            pass

    server = serve(NotJson)
    bk.BASE = f"http://127.0.0.1:{server.server_address[1]}"
    run_warm_up(bk)
    server.shutdown()


def test_local_server_thread_that_does_not_start_does_not_hang(bk, monkeypatch):
    class ThreadThatDoesNotStart(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    # Só o bootstrap vê a thread que não inicia; a do próprio teste continua normal.
    monkeypatch.setattr(bk, "threading", types.SimpleNamespace(Thread=ThreadThatDoesNotStart))
    run_warm_up(bk)


def test_ready_file_is_written_even_if_warm_up_raises_base_exception(bk, tmp_path, monkeypatch):
    ready = tmp_path / "keys-ready"
    monkeypatch.setattr(bk, "READY_FILE", str(ready))
    monkeypatch.setattr(bk, "wait_ready", lambda: None)
    monkeypatch.setattr(bk, "apply_keys", lambda: None)

    def interrupted():
        raise SystemExit("interrompido")

    monkeypatch.setattr(bk, "warm_up", interrupted)
    with pytest.raises(SystemExit):
        bk.main()
    assert ready.exists()
