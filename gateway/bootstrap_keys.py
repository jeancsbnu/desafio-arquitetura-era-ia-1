"""Aplica as chaves virtuais declaradas em keys.yaml e aquece o gateway, assim que ele fica pronto.

Roda dentro do container do gateway, em paralelo ao LiteLLM (ver start.sh):
1. para cada chave: cria se não existir, atualiza os limites se já existir;
2. aquece o gateway: a 1ª chamada real de um processo do LiteLLM paga uma inicialização única
   (medida entre ~0,5 e 3,7 s), qualquer que seja o destino. O bootstrap sobe por alguns segundos
   um servidor local que responde no formato da OpenAI e chama o destino `gateway-warmup`
   (config.yaml), que aponta para ele: a inicialização acontece aqui, e não na 1ª requisição de
   um cliente, sem chegar ao provider e sem nada em /admin/calls;
3. grava /tmp/keys-ready, que o healthcheck do serviço exige: o gateway só fica saudável depois.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

BASE = "http://127.0.0.1:4000"
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
KEYS_FILE = "/gateway/keys.yaml"
READY_FILE = "/tmp/keys-ready"
LIMIT_FIELDS = ("models", "max_budget", "budget_duration", "rpm_limit")
WARMUP_PORT = 4099  # o mesmo de `gateway-warmup` em config.yaml


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {MASTER_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


def resolve(value: str) -> str:
    return os.environ[value.removeprefix("os.environ/")] if value.startswith("os.environ/") else value


def wait_ready() -> None:
    for _ in range(180):
        try:
            status, body = call("GET", "/health/readiness")
            if status == 200 and body.get("db") == "connected":
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    sys.exit("gateway não ficou pronto com o banco conectado")


def apply_keys() -> None:
    with open(KEYS_FILE, encoding="utf-8") as f:
        declared = yaml.safe_load(f)["keys"]
    for item in declared:
        key = resolve(item["key"])
        limits = {field: item[field] for field in LIMIT_FIELDS if field in item}
        status, _ = call("GET", f"/key/info?key={key}")
        if status == 200:
            status, _ = call("POST", "/key/update", {"key": key, "key_alias": item["alias"], **limits})
            action = "atualizada"
        else:
            status, _ = call("POST", "/key/generate", {"key": key, "key_alias": item["alias"], **limits})
            action = "criada"
        if status != 200:
            sys.exit(f"falha ao aplicar a chave {item['alias']}: HTTP {status}")
        print(f"[bootstrap_keys] chave {item['alias']} {action}", flush=True)


class _CompletionStub(BaseHTTPRequestHandler):
    """Responde qualquer POST com uma completion mínima no formato da OpenAI."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({
            "id": "warmup", "object": "chat.completion", "created": int(time.time()),
            "model": "gateway-warmup",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # sem log de acesso
        pass


def warm_up() -> None:
    """Otimização: se algo falhar aqui, o gateway sobe do mesmo jeito, só com a 1ª chamada mais
    lenta."""
    server = ThreadingHTTPServer(("127.0.0.1", WARMUP_PORT), _CompletionStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start = time.perf_counter()
        status, _ = call("POST", "/chat/completions", {
            "model": "gateway-warmup", "messages": [{"role": "user", "content": "aquecimento"}],
        })
        print(f"[bootstrap_keys] aquecimento: HTTP {status} em {time.perf_counter() - start:.2f} s", flush=True)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        print(f"[bootstrap_keys] aquecimento falhou: {error}", flush=True)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    wait_ready()
    apply_keys()
    warm_up()
    open(READY_FILE, "w").close()


if __name__ == "__main__":
    main()
