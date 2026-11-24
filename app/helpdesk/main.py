import os

from fastapi import FastAPI

from .classification import TicketClassifier
from .extraction import OrderExtractor
from .gateway import GatewayLanguageModel
from .report import TopicsReporter
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)
from .suggestion import ReplySuggester
from .tickets import JsonlTicketSource

MAX_OUTPUT_TOKENS = 2000

# Ponto de composição: os adaptadores são montados aqui e entregues às features.
language_model = GatewayLanguageModel(
    base_url=os.environ["GATEWAY_URL"],
    api_key=os.environ["GATEWAY_API_KEY"],
    max_output_tokens=MAX_OUTPUT_TOKENS,
)
ticket_source = JsonlTicketSource(os.environ.get("TICKETS_FILE", "/data/tickets.jsonl"))

classifier = TicketClassifier(language_model)
suggester = ReplySuggester(language_model)
extractor = OrderExtractor(language_model)
reporter = TopicsReporter(language_model, ticket_source)

app = FastAPI(title="Helpdesk")


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
