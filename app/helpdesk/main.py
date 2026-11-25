import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .classification import TicketClassifier
from .extraction import OrderExtractor
from .gateway import GatewayLanguageModel
from .ports import ModelUnavailable
from .report import TopicsReporter
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)
from .suggestion import ReplySuggester
from .tickets import JsonlTicketSource

# Ponto de composição: os adaptadores são montados aqui e entregues às features.
language_model = GatewayLanguageModel(
    base_url=os.environ["GATEWAY_URL"],
    api_key=os.environ["GATEWAY_API_KEY"],
)
ticket_source = JsonlTicketSource(os.environ.get("TICKETS_FILE", "/data/tickets.jsonl"))

classifier = TicketClassifier(language_model)
suggester = ReplySuggester(language_model)
extractor = OrderExtractor(language_model)
reporter = TopicsReporter(language_model, ticket_source)

app = FastAPI(title="Helpdesk")


@app.exception_handler(ModelUnavailable)
def model_unavailable(request: Request, error: ModelUnavailable) -> JSONResponse:
    """Falha explícita: nenhum destino da capacidade respondeu (ADR de fallback)."""
    return JSONResponse(status_code=503, content={
        "detail": "Nenhum modelo disponível para atender a requisição. Tente novamente em instantes.",
        "capability": error.capability,
        "reason": error.reason,
    })


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
def classify_ticket(ticket: TicketInput) -> Classification:
    return classifier.classify(ticket)


@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> ReplySuggestion:
    return suggester.suggest(ticket)


@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extractor.extract(ticket)


@app.post("/reports/topics")
def topics_report(period: PeriodInput) -> TopicsReport:
    return reporter.topics(period)
