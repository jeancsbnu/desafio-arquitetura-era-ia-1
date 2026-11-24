"""F4 Extrair dados do pedido: POST /tickets/extraction.

Os casos com nota fiscal antes do número do pedido (TK-00002, TK-00015, TK-00036) e os que citam
produto são os que distinguem a saída de um modelo `large` da de um `mini`.
"""

import pytest

from helpers import EXAMPLE_TICKET, assert_json

ROUTE = "/tickets/extraction"


def test_example_ticket(client):
    response = client.post(ROUTE, json=EXAMPLE_TICKET)
    assert_json(response, 200, {"ticket_id": "TK-00042", "order_number": "#481516", "product": None})


@pytest.mark.parametrize(
    ("ticket_id", "order_number", "product"),
    [
        ("TK-00002", "#605065", None),                   # nota fiscal 530720 antes do pedido #605065
        ("TK-00004", "#732789", None),                   # "pedido nº 732789"
        ("TK-00007", None, "notebook 14 polegadas"),     # produto sem número de pedido
        ("TK-00015", "#972600", None),                   # nota fiscal 197527 antes do "pedido 972600"
        ("TK-00026", None, None),                        # nem pedido nem produto
        ("TK-00030", "#687783", "cortina blackout"),
        ("TK-00036", "#389511", "tapete de yoga"),       # nota fiscal 927477, pedido e produto
    ],
)
def test_dataset_tickets(client, tickets, ticket_id, order_number, product):
    response = client.post(ROUTE, json={"ticket_id": ticket_id, "text": tickets[ticket_id]["text"]})
    assert_json(response, 200, {"ticket_id": ticket_id, "order_number": order_number, "product": product})


def test_empty_ticket_id_is_rejected(client, no_provider_call):
    response = client.post(ROUTE, json={"ticket_id": "", "text": "Pedido #123456"})
    assert_json(response, 422, {"detail": [{
        "type": "string_too_short", "loc": ["body", "ticket_id"],
        "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1},
    }]})
