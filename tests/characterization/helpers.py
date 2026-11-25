import json
import time

import httpx

# Texto do ticket de exemplo do enunciado (não está em data/tickets.jsonl).
EXAMPLE_TICKET = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}


def assert_json(response: httpx.Response, status: int, body):
    """Fixa status, Content-Type e o corpo inteiro (campos e valores) da resposta."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.json() == body


def read_sse(response: httpx.Response) -> list[tuple[str, dict]]:
    """Os eventos de uma resposta Server-Sent Events, como (nome, dados). Sem `event:`, o nome é
    `message`, como no padrão SSE."""
    events, name, data = [], "message", []
    for line in response.text.split("\n"):
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].strip())
        elif line == "" and data:
            events.append((name, json.loads("\n".join(data))))
            name, data = "message", []
    return events


def wait_for_report(client: httpx.Client, location: str, timeout: float = 120) -> httpx.Response:
    """Consulta a URL de status até o `303` e devolve a resposta do status final (`303` ou
    `failed`). Falha o teste se passar de `timeout` segundos."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(location)
        if response.status_code != 200 or response.json()["state"] == "failed":
            return response
        assert response.json()["state"] in ("pending", "running"), response.text
        time.sleep(1)
    raise AssertionError(f"o relatório não terminou em {timeout:g} s")
