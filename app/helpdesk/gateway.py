"""Adaptador da porta `LanguageModel` para o AI Gateway.

É o único componente da aplicação que conhece um SDK de modelo. O gateway expõe a API no formato
da OpenAI para todas as capacidades, qualquer que seja o provider por trás de cada uma. Parâmetros
que dependem do provider (como o `max_tokens` obrigatório no formato Messages), tentativas e
fallbacks ficam na configuração do gateway, não aqui.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import openai
from openai import OpenAI

from .ports import LanguageModel, ModelRejectedInput, ModelUnavailable

log = logging.getLogger(__name__)


class GatewayLanguageModel(LanguageModel):
    """Declara a porta que implementa: o detalhe depende da abstração, e se faltar um método da
    porta o adaptador não pode ser instanciado."""

    def __init__(self, base_url: str, api_key: str):
        # Sem retry no SDK: as tentativas acontecem num lugar só, o gateway.
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def warm_up(self) -> None:
        """Abre a conexão com o gateway e inicializa o SDK antes da 1ª requisição real (custa
        ~0,4 s na 1ª chamada). Lista os modelos: não chega ao provider e não tem custo."""
        try:
            self._client.models.list(timeout=10)
        except openai.OpenAIError as error:
            log.warning("aquecimento da conexão com o gateway falhou: %s", error)

    def complete(self, capability: str, prompt: str, timeout: float) -> str:
        with self._translated_errors(capability, timeout):
            response = self._client.chat.completions.create(
                model=capability,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
        return response.choices[0].message.content

    def stream(self, capability: str, prompt: str, timeout: float) -> Iterator[str]:
        # A requisição é feita já aqui: uma falha antes do 1º trecho (o gateway esgotou os
        # destinos, recusou a chave) aparece nesta chamada, e não no meio da iteração.
        with self._translated_errors(capability, timeout):
            response = self._client.chat.completions.create(
                model=capability,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                timeout=timeout,
            )
        return self._chunks(capability, response)

    def _chunks(self, capability: str, response) -> Iterator[str]:
        try:
            for event in response:
                if event.choices and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except Exception as error:  # qualquer falha depois do 1º byte interrompe a geração
            raise self._unavailable(capability, f"geração interrompida ({type(error).__name__})") from error
        finally:
            response.close()

    @contextmanager
    def _translated_errors(self, capability: str, timeout: float):
        try:
            yield
        except openai.APITimeoutError as error:
            raise self._unavailable(capability, f"sem resposta do gateway em {timeout:g} s") from error
        except openai.BadRequestError as error:
            # 400: os destinos recusaram a entrada (ex.: maior que o contexto). Não é indisponibilidade.
            log.warning("capacidade %s recusou a entrada: HTTP 400", capability)
            raise ModelRejectedInput(capability, "a entrada foi recusada pelo modelo (HTTP 400)") from error
        except openai.APIStatusError as error:
            raise self._unavailable(capability, f"gateway respondeu HTTP {error.status_code}") from error
        except openai.APIConnectionError as error:
            raise self._unavailable(capability, "gateway inacessível") from error

    @staticmethod
    def _unavailable(capability: str, reason: str) -> ModelUnavailable:
        log.warning("capacidade %s indisponível: %s", capability, reason)
        return ModelUnavailable(capability, reason)
