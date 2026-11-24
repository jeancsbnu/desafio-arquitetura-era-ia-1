import httpx

# Texto do ticket de exemplo do enunciado (não está em data/tickets.jsonl).
EXAMPLE_TICKET = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}


def assert_json(response: httpx.Response, status: int, body):
    """Fixa status, Content-Type e o corpo inteiro (campos e valores) da resposta."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.json() == body
