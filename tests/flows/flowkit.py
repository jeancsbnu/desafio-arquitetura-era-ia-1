"""Ferramentas dos testes de fluxo: o provider simulado e a leitura de Server-Sent Events."""

import json
import os
import time

import httpx

PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")
TICKET = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}


class Provider:
    """Os endpoints de administração do provider simulado."""

    def fail(self, provider: str, mode: str, model: str | None = None) -> None:
        body = {"provider": provider, "mode": mode} | ({"model": model} if model else {})
        httpx.post(f"{PROVIDER_URL}/admin/failures", json=body, timeout=10).raise_for_status()

    def reset(self) -> None:
        httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=10).raise_for_status()

    def calls(self, task: str | None = None) -> list[tuple[str, int]]:
        """(modelo, status) das chamadas recebidas, da mais antiga para a mais recente."""
        calls = httpx.get(f"{PROVIDER_URL}/admin/calls", params={"last": 5000}, timeout=10).json()
        return [(c["model"], c["status"]) for c in reversed(calls) if task is None or c["task"] == task]


def sse_events(lines) -> list[tuple[str, dict, float]]:
    """Lê eventos Server-Sent Events de um iterador de linhas, com o instante de chegada de cada
    um (segundos desde `time.perf_counter()`)."""
    events, name, data = [], "message", []
    for line in lines:
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].strip())
        elif line == "" and data:
            events.append((name, json.loads("\n".join(data)), time.perf_counter()))
            name, data = "message", []
    return events
