"""Suíte de caracterização do helpdesk (requisito 2).

Fixa o comportamento observado da aplicação recebida (tag `v1-coupled`): status, Content-Type e
corpo das respostas das 4 features, sempre pela borda. A mesma suíte, sem alteração, precisa
passar na `v2-decoupled`.

Pré-requisito: o compose no ar (`docker compose up -d --build --wait`).
Execução: `python -m pytest tests/characterization` (ver "Como rodar" no README).
"""

import json
import os
from pathlib import Path

import httpx
import pytest

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://localhost:8000")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")

HERE = Path(__file__).parent
GOLDEN_DIR = HERE / "golden"
TICKETS_FILE = HERE.parents[1] / "data" / "tickets.jsonl"


@pytest.fixture(scope="session", autouse=True)
def provider_normal():
    """Começa a suíte com o provider simulado em modo normal (e o registro de chamadas limpo)."""
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=10).raise_for_status()


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=HELPDESK_URL, timeout=60) as c:
        yield c


@pytest.fixture(scope="session")
def tickets():
    """Os tickets de `data/tickets.jsonl`, por id."""
    with TICKETS_FILE.open(encoding="utf-8") as f:
        return {t["id"]: t for t in map(json.loads, f)}


@pytest.fixture(scope="session")
def golden():
    """Carrega uma resposta de referência gravada da `v1-coupled` em `golden/<nome>.json`."""
    def load(name: str):
        return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return load


def _last_provider_call_id():
    calls = httpx.get(f"{PROVIDER_URL}/admin/calls", params={"last": 1}, timeout=10).json()
    return calls[0]["id"] if calls else None


@pytest.fixture
def no_provider_call():
    """Falha o teste se alguma chamada de inferência chegar ao provider durante ele."""
    before = _last_provider_call_id()
    yield
    assert _last_provider_call_id() == before, "a requisição chegou ao provider"
