from collections.abc import Iterator

from .ports import LanguageModel
from .schemas import TicketInput

CAPABILITY = "reply-suggestion"
# Maior intervalo aceito sem receber nada do gateway, inclusive até o 1º trecho. Cobre a cadeia de
# destinos do gateway (5 s até o 1º trecho por destino) e fica abaixo dos 30 s da borda.
TIMEOUT_SECONDS = 20


class ReplySuggester:
    def __init__(self, model: LanguageModel):
        self._model = model

    def stream(self, ticket: TicketInput) -> Iterator[str]:
        """Os trechos da sugestão, à medida que são gerados (streaming, ADR 0008)."""
        return self._model.stream(CAPABILITY, f"TASK: suggest\n{ticket.text}", TIMEOUT_SECONDS)
