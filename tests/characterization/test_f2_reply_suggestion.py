"""F2 Sugerir resposta: POST /tickets/reply-suggestion.

Na v1 a resposta era síncrona: um JSON único com o texto completo. Na `main`, a F2 passou a
streaming (ADR 0008): eventos Server-Sent Events com os trechos e um evento `end`. O texto
concatenado continua igual ao gravado da v1 em `golden/`, então o conteúdo não mudou, só a forma
de entrega. Os erros de entrada continuam síncronos (`422`).
"""

from helpers import EXAMPLE_TICKET, assert_json, read_sse

ROUTE = "/tickets/reply-suggestion"


def assert_streamed_suggestion(response, expected: dict):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_sse(response)
    assert events[-1] == ("end", {"ticket_id": expected["ticket_id"]})
    chunks = [data["chunk"] for name, data in events[:-1] if name == "message"]
    assert len(chunks) == len(events) - 1 and len(chunks) > 1   # chegou em vários trechos
    assert "".join(chunks) == expected["suggestion"]


def test_example_ticket(client, golden):
    response = client.post(ROUTE, json=EXAMPLE_TICKET)
    assert_streamed_suggestion(response, golden("suggestion-TK-00042"))


def test_dataset_ticket(client, tickets, golden):
    response = client.post(ROUTE, json={"ticket_id": "TK-00058", "text": tickets["TK-00058"]["text"]})
    assert_streamed_suggestion(response, golden("suggestion-TK-00058"))


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
