import os

from fastapi import FastAPI

from . import classification, extraction, report, suggestion
from .gateway import GatewayLanguageModel
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)

MAX_OUTPUT_TOKENS = 2000

# Ponto de composição: os adaptadores são montados aqui e entregues às features.
language_model = GatewayLanguageModel(
    base_url=os.environ["GATEWAY_URL"],
    api_key=os.environ["GATEWAY_API_KEY"],
    max_output_tokens=MAX_OUTPUT_TOKENS,
)
classifier = classification.TicketClassifier(language_model)

app = FastAPI(title="Helpdesk")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
def classify_ticket(ticket: TicketInput) -> Classification:
    return classifier.classify(ticket)


@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> ReplySuggestion:
    return suggestion.suggest(ticket)


@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extraction.extract(ticket)


@app.post("/reports/topics")
def topics_report(period: PeriodInput) -> TopicsReport:
    return report.topics(period)
