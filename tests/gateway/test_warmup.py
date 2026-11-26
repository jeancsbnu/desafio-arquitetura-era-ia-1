"""Testes da subida do gateway (gateway/bootstrap_keys.py), sem Docker.

O aquecimento é uma otimização da subida: nenhuma falha nele pode impedir a gravação do arquivo
de pronto, que o healthcheck do gateway exige. Os testes de `warm_up()` trocam o endereço do
gateway por um servidor local de mentira e conferem que ele volta sem exceção, e rápido. Os de
`main()` conferem quando o arquivo de pronto é gravado. Os de `wait_ready()` conferem a espera
pelo gateway na 1ª subida: ela tolera o gateway ainda subindo e tem o mesmo prazo do healthcheck.

Execução: `python -m pytest tests/gateway` (só precisa do pytest).
"""

import importlib.util
import json
import socket
import sys
import threading
import time
import types
import urllib.request
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


class FakeGateway(BaseHTTPRequestHandler):
    """Base dos 'gateways' de mentira: lê o corpo do pedido inteiro antes de responder (sem isso,
    fechar a conexão com dados não lidos pode virar um reset no cliente) e não loga acessos."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.answer()

    def answer(self):
        raise NotImplementedError

    def reply(self, status: int, body: bytes = b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


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


def test_happy_path_goes_through_the_local_stub_once(bk, capsys):
    """O 'gateway' de mentira faz o que o LiteLLM faz: chama o destino gateway-warmup (o servidor
    local do bootstrap) e devolve a resposta dele."""
    forwarded = []

    class Gateway(FakeGateway):
        def answer(self):
            stub = f"http://127.0.0.1:{bk.WARMUP_PORT}/v1/chat/completions"
            with urllib.request.urlopen(urllib.request.Request(stub, data=b"{}", method="POST")) as r:
                body = r.read()
            forwarded.append(json.loads(body))
            self.reply(200, body)

    gateway = serve(Gateway)
    bk.BASE = f"http://127.0.0.1:{gateway.server_address[1]}"
    run_warm_up(bk)
    gateway.shutdown()
    assert len(forwarded) == 1
    assert forwarded[0]["choices"][0]["message"]["content"] == "ok"
    assert "aquecimento: HTTP 200" in capsys.readouterr().out
    # O servidor local foi desligado e fechou a porta: ninguém mais aceita conexão nela. Um `bind`
    # na mesma porta não serve para conferir isso: no Linux ele falha enquanto a conexão encerrada
    # fica em TIME_WAIT, mesmo com a porta já fechada.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", bk.WARMUP_PORT), timeout=1).close()


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


def test_gateway_answers_200_with_a_body_that_is_not_json(bk, capsys):
    class NotJson(FakeGateway):
        def answer(self):
            self.reply(200, b"oops")

    server = serve(NotJson)
    bk.BASE = f"http://127.0.0.1:{server.server_address[1]}"
    run_warm_up(bk)
    server.shutdown()
    assert "aquecimento falhou (JSONDecodeError" in capsys.readouterr().out


def test_gateway_answers_an_http_error_is_logged_as_a_failure(bk, capsys):
    class Error(FakeGateway):
        def answer(self):
            self.reply(500)

    server = serve(Error)
    bk.BASE = f"http://127.0.0.1:{server.server_address[1]}"
    run_warm_up(bk)
    server.shutdown()
    assert "aquecimento falhou (HTTP 500" in capsys.readouterr().out


def test_local_server_thread_that_does_not_start_does_not_hang(bk, monkeypatch):
    class ThreadThatDoesNotStart(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    # Só o bootstrap vê a thread que não inicia; a do próprio teste continua normal.
    monkeypatch.setattr(bk, "threading", types.SimpleNamespace(Thread=ThreadThatDoesNotStart))
    run_warm_up(bk)


@pytest.fixture
def startup(bk, tmp_path, monkeypatch):
    """main() com os três passos trocados por funções que registram a ordem em que rodaram."""
    ready, steps = tmp_path / "keys-ready", []
    monkeypatch.setattr(bk, "READY_FILE", str(ready))
    for name in ("wait_ready", "apply_keys", "warm_up"):
        monkeypatch.setattr(bk, name, lambda name=name: steps.append(name))
    return types.SimpleNamespace(bk=bk, ready=ready, steps=steps)


def test_ready_file_is_written_after_keys_and_warm_up(startup):
    startup.bk.main()
    assert startup.steps == ["wait_ready", "apply_keys", "warm_up"]
    assert startup.ready.exists()


def test_ready_file_is_written_even_if_warm_up_raises_base_exception(startup, monkeypatch):
    def interrupted():
        raise SystemExit("interrompido")

    monkeypatch.setattr(startup.bk, "warm_up", interrupted)
    with pytest.raises(SystemExit):
        startup.bk.main()
    assert startup.ready.exists()


def test_ready_file_is_not_written_when_the_keys_fail(startup, monkeypatch):
    """Sem as chaves o app não funciona: o gateway não pode ficar saudável."""
    def keys_fail():
        raise SystemExit("falha ao aplicar a chave helpdesk-app: HTTP 500")

    monkeypatch.setattr(startup.bk, "apply_keys", keys_fail)
    with pytest.raises(SystemExit):
        startup.bk.main()
    assert startup.steps == ["wait_ready"]
    assert not startup.ready.exists()


def test_wait_ready_waits_through_a_gateway_that_is_still_booting(bk, monkeypatch):
    """Enquanto sobe, o gateway pode cortar a conexão ou devolver um corpo vazio, inválido ou sem
    o banco conectado: nada disso derruba a espera, que só termina com o banco conectado."""
    answers = [None, (200, b""), (200, b"oops"), (200, b"[]"), (503, b'{"db": "connected"}'),
               (200, b'{"db": "disconnected"}'), (200, b'{"db": "connected"}')]
    served = []

    class Booting(FakeGateway):
        def do_GET(self):
            answer = answers[len(served)]
            served.append(answer)
            if answer is not None:  # None: fecha a conexão sem responder
                self.reply(*answer)

    server = serve(Booting)
    bk.BASE = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(bk, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None))
    bk.wait_ready()
    server.shutdown()
    assert served == answers


def test_wait_ready_gives_up_at_the_deadline(bk, monkeypatch):
    monkeypatch.setattr(bk, "READY_TIMEOUT", 1)
    start = time.perf_counter()
    with pytest.raises(SystemExit, match="em 1 s"):
        bk.wait_ready()
    assert time.perf_counter() - start < bk.READY_TIMEOUT + bk.CALL_TIMEOUT + 2


def test_ready_timeout_is_the_healthcheck_start_period(bk):
    """O bootstrap espera tanto quanto o healthcheck tolera: um prazo menor deixaria o gateway
    `unhealthy` numa máquina mais lenta, mesmo com o healthcheck ainda esperando."""
    compose = (MODULE.parents[1] / "compose.yaml").read_text(encoding="utf-8")
    assert f"start_period: {bk.READY_TIMEOUT}s" in compose
