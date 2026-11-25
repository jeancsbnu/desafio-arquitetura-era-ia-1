import json

from fastapi import HTTPException

from .ports import LanguageModel, Progress, TaskRunner, TicketSource
from .schemas import PeriodInput, TaskStatus, Topic, TopicsReport

CAPABILITY = "topics-report"
# Prazo por lote (~10 s no modelo large). Cobre a cadeia de destinos do gateway (até 3 × 15 s) e
# garante que um relatório sem nenhum destino disponível chegue a `failed` em menos de 60 s.
TIMEOUT_SECONDS = 50
TICKETS_PER_CALL = 150


class TopicsReporter:
    """F3: o pedido é aceito na hora e o relatório é produzido em segundo plano (ADR 0008)."""

    def __init__(self, model: LanguageModel, tickets: TicketSource, tasks: TaskRunner):
        self._model = model
        self._tickets = tickets
        self._tasks = tasks

    def request(self, period: PeriodInput) -> str:
        """Valida o período e agenda o relatório; devolve o id da tarefa."""
        if period.start > period.end:
            raise HTTPException(status_code=422, detail="A data inicial é posterior à data final")
        return self._tasks.submit(lambda progress: self.topics(period, progress))

    def status(self, task_id: str) -> TaskStatus | None:
        return self._tasks.status(task_id)

    def result(self, task_id: str) -> TopicsReport | None:
        return self._tasks.result(task_id)

    def topics(self, period: PeriodInput, progress: Progress = lambda _: None) -> TopicsReport:
        selected = self._tickets.in_period(period.start, period.end)

        # O mês inteiro não cabe numa chamada: processa em lotes, um depois do outro.
        totals: dict[str, dict] = {}
        for first in range(0, len(selected), TICKETS_PER_CALL):
            batch = selected[first:first + TICKETS_PER_CALL]
            content = "\n".join(f"[{t['id']}] {t['text']}" for t in batch)
            answer = json.loads(self._model.complete(CAPABILITY, f"TASK: topics\n{content}", TIMEOUT_SECONDS))
            for item in answer["topics"]:
                current = totals.setdefault(item["topic"], {"count": 0, "examples": []})
                current["count"] += item["count"]
                current["examples"] = (current["examples"] + item["examples"])[:3]
            progress(f"{first + len(batch)}/{len(selected)}")

        items = [Topic(topic=name, **data) for name, data in totals.items()]
        items.sort(key=lambda t: (-t.count, t.topic))
        return TopicsReport(start=period.start, end=period.end, total_tickets=len(selected), topics=items)
