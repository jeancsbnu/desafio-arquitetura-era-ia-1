"""Critérios de "Fluxos de chamada" e de "Gateway" do enunciado, medidos pela borda."""

import time

import pytest

from flowkit import TICKET, sse_events

MONTH = {"start": "2026-08-01", "end": "2026-08-31"}
DAY = {"start": "2026-08-01", "end": "2026-08-01"}


def stream_suggestion(client):
    """Chama a F2 e devolve (status, content-type, eventos com instante, início, fim)."""
    start = time.perf_counter()
    with client.stream("POST", "/tickets/reply-suggestion", json=TICKET) as response:
        events = sse_events(response.iter_lines())
        return response.status_code, response.headers["content-type"], events, start, time.perf_counter()


def wait_status(client, location: str, timeout: float):
    """Consulta o status até sair de pending/running; devolve (resposta, segundos esperados)."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        response = client.get(location)
        if response.status_code != 200 or response.json()["state"] not in ("pending", "running"):
            return response, time.perf_counter() - start
        time.sleep(1)
    raise AssertionError(f"a tarefa continuava pendente depois de {timeout:g} s")


# --- Modo normal --------------------------------------------------------------------------------

def test_f1_delivers_full_result_within_3_s(client, provider):
    start = time.perf_counter()
    response = client.post("/tickets/classification", json=TICKET)
    assert time.perf_counter() - start <= 3
    assert response.status_code == 200
    assert response.json() == {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}


def test_f2_first_chunk_within_1_5_s_and_before_half_of_total(client, provider):
    status, content_type, events, start, end = stream_suggestion(client)
    assert status == 200 and content_type.startswith("text/event-stream")
    first_chunk_at = events[0][2] - start
    assert events[0][0] == "message" and "chunk" in events[0][1]
    assert first_chunk_at <= 1.5
    assert first_chunk_at < (end - start) / 2
    assert events[-1][:2] == ("end", {"ticket_id": "TK-00042"})


def test_f3_accepts_month_within_1_s_and_reports_progress(client, provider):
    start = time.perf_counter()
    response = client.post("/reports/topics", json=MONTH)
    assert time.perf_counter() - start <= 1
    assert response.status_code == 202
    assert response.headers["location"].startswith("/reports/topics/status/")
    assert response.headers["retry-after"] == "5"
    status = client.get(response.headers["location"])
    assert status.status_code == 200 and status.json()["state"] in ("pending", "running")


# --- Falhas do provider -------------------------------------------------------------------------

def test_primary_provider_down_falls_back_to_the_other_provider(client, provider):
    provider.fail("openai", "error_500")
    response = client.post("/tickets/classification", json=TICKET)
    assert response.status_code == 200
    calls = provider.calls("classify")
    assert calls[-1] == ("claude-fake-large", 200)
    assert calls[:-1] == [("gpt-fake-large", 500)] * len(calls[:-1]) and 1 <= len(calls[:-1]) <= 3


def test_f1_with_primary_hanging_answers_within_15_s(client, provider):
    provider.fail("openai", "timeout")
    start = time.perf_counter()
    response = client.post("/tickets/classification", json=TICKET)
    assert time.perf_counter() - start <= 15
    assert response.status_code == 200                     # sucesso pelo fallback técnico
    assert provider.calls("classify") == [("gpt-fake-large", 504), ("claude-fake-large", 200)]


def test_f2_error_in_the_middle_of_the_stream_becomes_error_event(client, provider):
    provider.fail("anthropic", "midstream_error")
    status, _, events, _, _ = stream_suggestion(client)
    assert status == 200
    assert any(name == "message" for name, _, _ in events)      # parte do texto chegou
    assert events[-1][:2] == ("error", {"ticket_id": "TK-00042", "message": "A geração foi interrompida"})


def test_f3_fails_with_reason_within_60_s_when_both_providers_are_down(client, provider):
    provider.fail("openai", "error_500")
    provider.fail("anthropic", "error_500")
    response = client.post("/reports/topics", json=MONTH)
    final, waited = wait_status(client, response.headers["location"], timeout=60)
    assert waited <= 60
    assert final.status_code == 200
    assert final.json()["state"] == "failed" and final.json()["reason"]


def test_without_large_models_each_feature_follows_the_fallback_table(client, provider):
    provider.fail("openai", "error_500", model="gpt-fake-large")
    provider.fail("anthropic", "error_500", model="claude-fake-large")

    f1 = client.post("/tickets/classification", json=TICKET)
    assert f1.status_code == 200 and provider.calls("classify")[-1] == ("gpt-fake-mini", 200)

    status, _, events, _, _ = stream_suggestion(client)
    assert status == 200 and events[-1][0] == "end"
    assert provider.calls("suggest")[-1] == ("claude-fake-mini", 200)

    report = client.post("/reports/topics", json=DAY)
    final, _ = wait_status(client, report.headers["location"], timeout=60)
    assert final.status_code == 303 and provider.calls("topics")[-1] == ("claude-fake-mini", 200)

    f4 = client.post("/tickets/extraction", json=TICKET)
    assert f4.status_code == 503 and f4.json()["capability"] == "order-extraction"
    assert not any(model.endswith("-mini") for model, _ in provider.calls("extract"))


# --- Relatório do mês inteiro (~6 min) ----------------------------------------------------------

@pytest.mark.slow
def test_f3_month_report_covers_5000_tickets(client, provider):
    response = client.post("/reports/topics", json=MONTH)
    final, _ = wait_status(client, response.headers["location"], timeout=900)
    assert final.status_code == 303, final.text
    report = client.get(final.headers["location"])
    assert report.status_code == 200 and report.json()["total_tickets"] == 5000
    # 5.000 tickets em lotes de 150 = 34 chamadas. "Pelo menos": um relatório pedido por outro
    # teste (o do 202) pode ainda estar rodando em segundo plano, e o provider não distingue tarefas.
    assert len(provider.calls("topics")) >= 34
