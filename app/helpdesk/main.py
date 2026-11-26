import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from .background import ThreadTaskRunner
from .classification import TicketClassifier
from .extraction import OrderExtractor
from .gateway import GatewayLanguageModel
from .ports import ModelRejectedInput, ModelUnavailable
from .report import TopicsReporter
from .schemas import Classification, OrderData, PeriodInput, TicketInput, TopicsReport
from .suggestion import ReplySuggester
from .tickets import JsonlTicketSource

# Ponto de composição: os adaptadores são montados aqui e entregues às features.
language_model = GatewayLanguageModel(
    base_url=os.environ["GATEWAY_URL"],
    api_key=os.environ["GATEWAY_API_KEY"],
)
ticket_source = JsonlTicketSource(os.environ.get("TICKETS_FILE", "/data/tickets.jsonl"))
# 4 relatórios ao mesmo tempo; os 100 últimos terminados continuam consultáveis.
task_runner = ThreadTaskRunner(max_workers=4, keep_finished=100)

classifier = TicketClassifier(language_model)
suggester = ReplySuggester(language_model)
extractor = OrderExtractor(language_model)
reporter = TopicsReporter(language_model, ticket_source, task_runner)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # O 1º trecho da F2 tem 1,5 s de prazo: a conexão com o gateway já sai aberta da subida.
    await run_in_threadpool(language_model.warm_up)
    yield


app = FastAPI(title="Helpdesk", lifespan=lifespan)

REPORT_RETRY_AFTER_SECONDS = 5
# O nginx da borda acumula a resposta por padrão; este header desliga o buffer para o streaming.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.exception_handler(ModelUnavailable)
def model_unavailable(request: Request, error: ModelUnavailable) -> JSONResponse:
    """Falha explícita: nenhum destino da capacidade respondeu (ADR de fallback)."""
    return JSONResponse(status_code=503, content={
        "detail": "Nenhum modelo disponível para atender a requisição. Tente novamente em instantes.",
        "capability": error.capability,
        "reason": error.reason,
    })


@app.exception_handler(ModelRejectedInput)
def model_rejected_input(request: Request, error: ModelRejectedInput) -> JSONResponse:
    """A entrada não pode ser processada por nenhum destino (ex.: texto maior que o contexto)."""
    return JSONResponse(status_code=422, content={
        "detail": "O texto não pode ser processado pelo modelo (por exemplo, é longo demais).",
        "capability": error.capability,
        "reason": error.reason,
    })


@app.get("/health")
def health():
    return {"status": "ok"}


# F1: síncrona.
@app.post("/tickets/classification")
def classify_ticket(ticket: TicketInput) -> Classification:
    return classifier.classify(ticket)


# F2: streaming (Server-Sent Events).
@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> StreamingResponse:
    chunks = suggester.stream(ticket)
    # Até o 1º trecho, uma falha ainda vira status HTTP (503/422, pelos handlers acima). Depois
    # dele, o 200 já foi enviado, e a falha vira o evento `error` dentro do stream.
    first = next(chunks, None)
    return StreamingResponse(_sse(ticket.ticket_id, first, chunks), media_type="text/event-stream",
                             headers=SSE_HEADERS)


def _sse(ticket_id: str, first: str | None, rest: Iterator[str]) -> Iterator[str]:
    try:
        if first is not None:
            yield _event({"chunk": first})
        for chunk in rest:
            yield _event({"chunk": chunk})
    except (ModelUnavailable, ModelRejectedInput):
        yield _event({"ticket_id": ticket_id, "message": "A geração foi interrompida"}, name="error")
        return
    yield _event({"ticket_id": ticket_id}, name="end")


def _event(data: dict, name: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n" if name else f"data: {payload}\n\n"


# F4: síncrona.
@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extractor.extract(ticket)


# F3: assíncrona (Asynchronous Request-Reply).
@app.post("/reports/topics", status_code=202)
def request_topics_report(period: PeriodInput) -> JSONResponse:
    task_id = reporter.request(period)
    return JSONResponse(status_code=202, content={"task_id": task_id, "state": "pending"}, headers={
        "Location": f"/reports/topics/status/{task_id}",
        "Retry-After": str(REPORT_RETRY_AFTER_SECONDS),
    })


@app.get("/reports/topics/status/{task_id}", response_model=None)
def topics_report_status(task_id: str) -> JSONResponse | RedirectResponse:
    status = reporter.status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    if status.state == "done":
        return RedirectResponse(f"/reports/topics/{task_id}", status_code=303)
    headers = {} if status.state == "failed" else {"Retry-After": str(REPORT_RETRY_AFTER_SECONDS)}
    return JSONResponse(status.model_dump(exclude_none=True), headers=headers)


@app.get("/reports/topics/{task_id}")
def topics_report_result(task_id: str) -> TopicsReport:
    report = reporter.result(task_id)
    if report is not None:
        return report
    status = reporter.status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    if status.state == "failed":
        raise HTTPException(status_code=409, detail="O relatório não foi produzido: a tarefa falhou; o motivo está na URL de status")
    raise HTTPException(status_code=409, detail="O relatório ainda não está pronto; consulte o status")
