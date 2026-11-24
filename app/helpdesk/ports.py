"""Portas: o que as features precisam do mundo externo, sem saber quem o implementa."""

from typing import Protocol


class LanguageModel(Protocol):
    """Gera texto para uma capacidade lógica. Qual modelo físico responde é decisão do gateway."""

    def complete(self, capability: str, prompt: str) -> str: ...
