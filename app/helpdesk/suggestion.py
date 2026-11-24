from .ports import LanguageModel
from .schemas import ReplySuggestion, TicketInput

CAPABILITY = "reply-suggestion"


class ReplySuggester:
    def __init__(self, model: LanguageModel):
        self._model = model

    def suggest(self, ticket: TicketInput) -> ReplySuggestion:
        text = self._model.complete(CAPABILITY, f"TASK: suggest\n{ticket.text}")
        return ReplySuggestion(ticket_id=ticket.ticket_id, suggestion=text)
