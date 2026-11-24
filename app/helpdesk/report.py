import json

from fastapi import HTTPException

from .ports import LanguageModel, TicketSource
from .schemas import PeriodInput, Topic, TopicsReport

CAPABILITY = "topics-report"
TICKETS_PER_CALL = 150


class TopicsReporter:
    def __init__(self, model: LanguageModel, tickets: TicketSource):
        self._model = model
        self._tickets = tickets

    def topics(self, period: PeriodInput) -> TopicsReport:
        if period.start > period.end:
            raise HTTPException(status_code=422, detail="A data inicial é posterior à data final")

        selected = self._tickets.in_period(period.start, period.end)

        # O mês inteiro não cabe numa chamada: processa em lotes, um depois do outro.
        totals: dict[str, dict] = {}
        for first in range(0, len(selected), TICKETS_PER_CALL):
            batch = selected[first:first + TICKETS_PER_CALL]
            content = "\n".join(f"[{t['id']}] {t['text']}" for t in batch)
            answer = json.loads(self._model.complete(CAPABILITY, f"TASK: topics\n{content}"))
            for item in answer["topics"]:
                current = totals.setdefault(item["topic"], {"count": 0, "examples": []})
                current["count"] += item["count"]
                current["examples"] = (current["examples"] + item["examples"])[:3]

        items = [Topic(topic=name, **data) for name, data in totals.items()]
        items.sort(key=lambda t: (-t.count, t.topic))
        return TopicsReport(start=period.start, end=period.end, total_tickets=len(selected), topics=items)
