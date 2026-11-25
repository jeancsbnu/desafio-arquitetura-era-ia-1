"""Portas: o que as features precisam do mundo externo, sem saber quem o implementa.

Os métodos são abstratos: um adaptador que declara a porta e não implementa um deles não pode
ser instanciado (TypeError), e a aplicação não sobe.
"""

from abc import abstractmethod
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any, Protocol

from .schemas import TaskStatus


class ModelUnavailable(Exception):
    """Nenhum destino da capacidade respondeu dentro do prazo: o gateway esgotou o primário e os
    fallbacks, recusou a chamada (orçamento, limite) ou não respondeu a tempo. Também é levantada
    quando a geração é interrompida no meio de um streaming."""

    def __init__(self, capability: str, reason: str):
        super().__init__(f"{capability}: {reason}")
        self.capability = capability
        self.reason = reason


class ModelRejectedInput(Exception):
    """Os destinos da capacidade recusaram a entrada (ex.: maior que o contexto do modelo).
    Tentar de novo não adianta: é a entrada que precisa mudar."""

    def __init__(self, capability: str, reason: str):
        super().__init__(f"{capability}: {reason}")
        self.capability = capability
        self.reason = reason


class LanguageModel(Protocol):
    """Gera texto para uma capacidade lógica. Qual modelo físico responde é decisão do gateway."""

    @abstractmethod
    def complete(self, capability: str, prompt: str, timeout: float) -> str:
        """Devolve o texto gerado. Levanta ModelUnavailable se não houver resposta em `timeout`
        segundos, e ModelRejectedInput se os destinos recusarem a entrada."""

    @abstractmethod
    def stream(self, capability: str, prompt: str, timeout: float) -> Iterator[str]:
        """Devolve o texto em trechos, à medida que é gerado. `timeout` é o maior intervalo aceito
        sem receber nada. As mesmas exceções de `complete`, inclusive no meio da iteração."""


class TicketSource(Protocol):
    """Fornece os tickets de um período (datas inclusivas)."""

    @abstractmethod
    def in_period(self, start: date, end: date) -> list[dict]: ...


Progress = Callable[[str], None]


class TaskRunner(Protocol):
    """Executa trabalho em segundo plano e guarda o estado e o resultado de cada tarefa."""

    @abstractmethod
    def submit(self, work: Callable[[Progress], Any]) -> str:
        """Agenda `work(progress)` e devolve o id da tarefa. Se `work` levantar uma exceção, a
        tarefa termina em `failed`, com a mensagem da exceção como motivo."""

    @abstractmethod
    def status(self, task_id: str) -> TaskStatus | None:
        """Estado atual da tarefa, ou None se o id não existe."""

    @abstractmethod
    def result(self, task_id: str) -> Any | None:
        """O que `work` devolveu, se a tarefa terminou em `done`; senão None."""
