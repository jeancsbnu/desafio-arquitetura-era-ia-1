"""Testes dos fluxos da `main` (requisito 6): prazos medidos pela borda e cenários de falha do
provider simulado, conferidos em `/admin/calls`.

Pré-requisito: o compose no ar. Execução: `python -m pytest tests/flows` (ver "Como rodar" no
README). O teste do relatório do mês inteiro leva ~6 min e tem o marcador `slow`:
`python -m pytest tests/flows -m "not slow"` o deixa de fora.
"""

import os

import httpx
import pytest

from flowkit import Provider

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://localhost:8000")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: leva minutos (o relatório do mês inteiro)")


@pytest.fixture
def provider():
    """Provider em modo normal e registro limpo no início e no fim de cada teste."""
    p = Provider()
    p.reset()
    yield p
    p.reset()


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=HELPDESK_URL, timeout=120) as c:
        yield c
