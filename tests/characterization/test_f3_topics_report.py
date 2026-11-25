"""F3 Relatório de temas: POST /reports/topics.

Na v1 o relatório era síncrono, e o mês inteiro passava dos 30 s da borda. Na `main`, a F3 passou
a assíncrona (Asynchronous Request-Reply, ADR 0008): `202` com `Location` e `Retry-After`, a URL
de status responde o estado e, ao terminar, `303` para o resultado. O relatório obtido continua
igual ao gravado da v1 em `golden/`. Os erros de entrada continuam síncronos (`422`).
"""

from helpers import assert_json, wait_for_report

ROUTE = "/reports/topics"


def request_report(client, start: str, end: str):
    response = client.post(ROUTE, json={"start": start, "end": end})
    assert response.status_code == 202, response.text
    assert response.headers["location"].startswith("/reports/topics/status/")
    assert int(response.headers["retry-after"]) > 0
    assert response.json()["state"] == "pending"
    return response.headers["location"]


def fetch_report(client, start: str, end: str):
    final = wait_for_report(client, request_report(client, start, end))
    assert final.status_code == 303, final.text
    return client.get(final.headers["location"])


def test_one_day(client, golden):
    assert_json(fetch_report(client, "2026-08-01", "2026-08-01"), 200, golden("topics-2026-08-01"))


def test_two_days_merges_batches(client, golden):
    assert_json(fetch_report(client, "2026-08-01", "2026-08-02"), 200, golden("topics-2026-08-01_2026-08-02"))


def test_period_without_tickets(client, no_provider_call):
    assert_json(fetch_report(client, "2026-09-01", "2026-09-30"), 200,
                {"start": "2026-09-01", "end": "2026-09-30", "total_tickets": 0, "topics": []})


def test_unknown_task_is_not_found(client):
    response = client.get("/reports/topics/status/nao-existe")
    assert_json(response, 404, {"detail": "Tarefa não encontrada"})


def test_start_after_end_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"start": "2026-08-31", "end": "2026-08-01"})
    assert_json(response, 422, {"detail": "A data inicial é posterior à data final"})


def test_invalid_date_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"start": "2026-02-30", "end": "2026-08-01"})
    assert_json(response, 422, {"detail": [{
        "type": "date_from_datetime_parsing", "loc": ["body", "start"],
        "msg": "Input should be a valid date or datetime, day value is outside expected range",
        "input": "2026-02-30", "ctx": {"error": "day value is outside expected range"},
    }]})


def test_missing_end_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"start": "2026-08-01"})
    assert_json(response, 422, {"detail": [{
        "type": "missing", "loc": ["body", "end"], "msg": "Field required", "input": {"start": "2026-08-01"},
    }]})
