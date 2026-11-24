"""Portas: o que as features precisam do mundo externo, sem saber quem o implementa.

Os métodos são abstratos: um adaptador que declara a porta e não implementa um deles não pode
ser instanciado (TypeError), e a aplicação não sobe.
"""

from abc import abstractmethod
from datetime import date
from typing import Protocol


class LanguageModel(Protocol):
    """Gera texto para uma capacidade lógica. Qual modelo físico responde é decisão do gateway."""

    @abstractmethod
    def complete(self, capability: str, prompt: str) -> str: ...


class TicketSource(Protocol):
    """Fornece os tickets de um período (datas inclusivas)."""

    @abstractmethod
    def in_period(self, start: date, end: date) -> list[dict]: ...
