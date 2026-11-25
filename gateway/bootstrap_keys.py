"""Aplica as chaves virtuais declaradas em keys.yaml assim que o gateway fica pronto.

Roda dentro do container do gateway, em paralelo ao LiteLLM (ver start.sh). Para cada chave:
cria se não existir, atualiza os limites se já existir. No fim, grava /tmp/keys-ready, que o
healthcheck do serviço exige: o gateway só fica saudável com as chaves aplicadas.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml

BASE = "http://127.0.0.1:4000"
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
KEYS_FILE = "/gateway/keys.yaml"
READY_FILE = "/tmp/keys-ready"
LIMIT_FIELDS = ("models", "max_budget", "budget_duration", "rpm_limit")


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


def main() -> None:
    wait_ready()
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
    open(READY_FILE, "w").close()


if __name__ == "__main__":
    main()
