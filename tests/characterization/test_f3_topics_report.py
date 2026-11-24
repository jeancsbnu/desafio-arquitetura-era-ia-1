"""F3 Relatório de temas: POST /reports/topics.

Na v1 o relatório é síncrono e o mês inteiro passa dos 30 s da borda (ver "Diagnóstico da v1"),
por isso a suíte fixa períodos curtos: um dia (um lote de até 150 tickets) e dois dias (274
tickets, dois lotes), que fixa também como os lotes são somados.
"""

from helpers import assert_json

ROUTE = "/reports/topics"


def test_one_day(client, golden):
    response = client.post(ROUTE, json={"start": "2026-08-01", "end": "2026-08-01"})
    assert_json(response, 200, golden("topics-2026-08-01"))


def test_two_days_merges_batches(client, golden):
    response = client.post(ROUTE, json={"start": "2026-08-01", "end": "2026-08-02"})
    assert_json(response, 200, golden("topics-2026-08-01_2026-08-02"))


def test_period_without_tickets(client, no_provider_call):
    response = client.post(ROUTE, json={"start": "2026-09-01", "end": "2026-09-30"})
    assert_json(response, 200, {"start": "2026-09-01", "end": "2026-09-30", "total_tickets": 0, "topics": []})


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
