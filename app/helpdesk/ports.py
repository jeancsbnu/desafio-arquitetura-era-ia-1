"""Portas: o que as features precisam do mundo externo, sem saber quem o implementa.

Os métodos são abstratos: um adaptador que declara a porta e não implementa um deles não pode
ser instanciado (TypeError), e a aplicação não sobe.
"""

from abc import abstractmethod
from datetime import date
from typing import Protocol


class ModelUnavailable(Exception):
    """Nenhum destino da capacidade respondeu dentro do prazo: o gateway esgotou o primário e os
    fallbacks, recusou a chamada (orçamento, limite) ou não respondeu a tempo."""

    def __init__(self, capability: str, reason: str):
        super().__init__(f"{capability}: {reason}")
        self.capability = capability
        self.reason = reason


class LanguageModel(Protocol):
    """Gera texto para uma capacidade lógica. Qual modelo físico responde é decisão do gateway."""

    @abstractmethod
    def complete(self, capability: str, prompt: str, timeout: float) -> str:
        """Devolve o texto gerado, ou levanta ModelUnavailable se não houver resposta em `timeout`
        segundos."""


class TicketSource(Protocol):
    """Fornece os tickets de um período (datas inclusivas)."""

    @abstractmethod
    def in_period(self, start: date, end: date) -> list[dict]: ...
