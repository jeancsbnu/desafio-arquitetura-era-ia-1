"""F2 Sugerir resposta: POST /tickets/reply-suggestion.

Na v1 a resposta é síncrona: um JSON único com o texto completo. O texto é longo (~2.400
caracteres), por isso fica gravado em `golden/`.
"""

from helpers import EXAMPLE_TICKET, assert_json

ROUTE = "/tickets/reply-suggestion"


def test_example_ticket(client, golden):
    response = client.post(ROUTE, json=EXAMPLE_TICKET)
    assert_json(response, 200, golden("suggestion-TK-00042"))


def test_dataset_ticket(client, tickets, golden):
    response = client.post(ROUTE, json={"ticket_id": "TK-00058", "text": tickets["TK-00058"]["text"]})
    assert_json(response, 200, golden("suggestion-TK-00058"))


def test_missing_text_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"ticket_id": "TK-1"})
    assert_json(response, 422, {"detail": [{
        "type": "missing", "loc": ["body", "text"], "msg": "Field required", "input": {"ticket_id": "TK-1"},
    }]})


def test_non_string_text_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"ticket_id": "TK-1", "text": 123})
    assert_json(response, 422, {"detail": [{
        "type": "string_type", "loc": ["body", "text"], "msg": "Input should be a valid string", "input": 123,
    }]})
