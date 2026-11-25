"""Adaptador da porta `TaskRunner`: tarefas em segundo plano num pool de threads, com o estado em
memória.

Basta para o volume do helpdesk e não exige fila, banco nem worker no compose (ADR de execução).
O estado se perde se a aplicação reiniciar, o que o enunciado deixa fora do escopo, e só vale com
um processo (o uvicorn sobe um só).
"""

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .ports import Progress, TaskRunner
from .schemas import TaskStatus

log = logging.getLogger(__name__)


class ThreadTaskRunner(TaskRunner):
    """Declara a porta que implementa."""

    def __init__(self, max_workers: int):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="task")
        self._lock = threading.Lock()
        self._status: dict[str, TaskStatus] = {}
        self._results: dict[str, Any] = {}

    def submit(self, work: Callable[[Progress], Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._status[task_id] = TaskStatus(state="pending")
        self._executor.submit(self._run, task_id, work)
        return task_id

    def status(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            return self._status.get(task_id)

    def result(self, task_id: str) -> Any | None:
        with self._lock:
            return self._results.get(task_id)

    def _run(self, task_id: str, work: Callable[[Progress], Any]) -> None:
        self._set(task_id, TaskStatus(state="running"))
        try:
            result = work(lambda progress: self._set(task_id, TaskStatus(state="running", progress=progress)))
        except Exception as error:  # nenhuma tarefa fica presa em `running`
            log.warning("tarefa %s falhou: %s", task_id, error)
            self._set(task_id, TaskStatus(state="failed", reason=str(error) or type(error).__name__))
            return
        with self._lock:
            self._results[task_id] = result
            self._status[task_id] = TaskStatus(state="done")

    def _set(self, task_id: str, status: TaskStatus) -> None:
        with self._lock:
            self._status[task_id] = status
