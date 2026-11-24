import json

from fastapi import HTTPException

from .ports import LanguageModel
from .schemas import Classification, TicketInput

CAPABILITY = "ticket-classification"
CATEGORIES = {"delivery", "payment", "exchange_return", "product_defect", "other"}
PRIORITIES = {"low", "medium", "high"}


class TicketClassifier:
    def __init__(self, model: LanguageModel):
        self._model = model

    def classify(self, ticket: TicketInput) -> Classification:
        text = self._model.complete(CAPABILITY, f"TASK: classify\n{ticket.text}")
        data = json.loads(text)
        if data.get("category") not in CATEGORIES or data.get("priority") not in PRIORITIES:
            raise HTTPException(status_code=502, detail="Classificação fora dos valores permitidos")
        return Classification(ticket_id=ticket.ticket_id, **data)
