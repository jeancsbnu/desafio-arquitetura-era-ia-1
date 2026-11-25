from .ports import LanguageModel
from .schemas import ReplySuggestion, TicketInput

CAPABILITY = "reply-suggestion"
# Prazo para a sugestão completa (~16 s no modelo large), abaixo dos 30 s da borda.
TIMEOUT_SECONDS = 28


class ReplySuggester:
    def __init__(self, model: LanguageModel):
        self._model = model

    def suggest(self, ticket: TicketInput) -> ReplySuggestion:
        text = self._model.complete(CAPABILITY, f"TASK: suggest\n{ticket.text}", TIMEOUT_SECONDS)
        return ReplySuggestion(ticket_id=ticket.ticket_id, suggestion=text)
