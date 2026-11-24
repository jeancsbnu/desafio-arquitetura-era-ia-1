"""F1 Classificar ticket: POST /tickets/classification."""

import pytest

from helpers import EXAMPLE_TICKET, assert_json

ROUTE = "/tickets/classification"


def test_example_ticket(client):
    response = client.post(ROUTE, json=EXAMPLE_TICKET)
    assert_json(response, 200, {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"})


@pytest.mark.parametrize(
    ("ticket_id", "category", "priority"),
    [
        ("TK-00002", "product_defect", "medium"),
        ("TK-00009", "delivery", "high"),
        ("TK-00010", "delivery", "medium"),
        ("TK-00012", "other", "low"),
        ("TK-00022", "exchange_return", "medium"),
        ("TK-00026", "other", "medium"),
        ("TK-00058", "payment", "high"),
    ],
)
def test_dataset_tickets(client, tickets, ticket_id, category, priority):
    response = client.post(ROUTE, json={"ticket_id": ticket_id, "text": tickets[ticket_id]["text"]})
    assert_json(response, 200, {"ticket_id": ticket_id, "category": category, "priority": priority})


def test_empty_text_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"ticket_id": "TK-1", "text": ""})
    assert_json(response, 422, {"detail": [{
        "type": "string_too_short", "loc": ["body", "text"],
        "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1},
    }]})


def test_missing_ticket_id_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"text": "oi"})
    assert_json(response, 422, {"detail": [{
        "type": "missing", "loc": ["body", "ticket_id"], "msg": "Field required", "input": {"text": "oi"},
    }]})


def test_malformed_json_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, content=b'{"ticket_id": "TK-1", "text": ',
                           headers={"Content-Type": "application/json"})
    assert_json(response, 422, {"detail": [{
        "type": "json_invalid", "loc": ["body", 30], "msg": "JSON decode error",
        "input": {}, "ctx": {"error": "Expecting value"},
    }]})
