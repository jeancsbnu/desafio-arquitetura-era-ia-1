"""Adaptador da porta `LanguageModel` para o AI Gateway.

É o único componente da aplicação que conhece um SDK de modelo. O gateway expõe a API no formato
da OpenAI para todas as capacidades, qualquer que seja o provider por trás de cada uma. Parâmetros
que dependem do provider (como o `max_tokens` obrigatório no formato Messages), tentativas e
fallbacks ficam na configuração do gateway, não aqui.
"""

import logging

import openai
from openai import OpenAI

from .ports import LanguageModel, ModelUnavailable

log = logging.getLogger(__name__)


class GatewayLanguageModel(LanguageModel):
    """Declara a porta que implementa: o detalhe depende da abstração, e se faltar um método da
    porta o adaptador não pode ser instanciado."""

    def __init__(self, base_url: str, api_key: str):
        # Sem retry no SDK: as tentativas acontecem num lugar só, o gateway.
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def complete(self, capability: str, prompt: str, timeout: float) -> str:
        try:
            response = self._client.chat.completions.create(
                model=capability,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
        except openai.APITimeoutError as error:
            raise self._unavailable(capability, f"sem resposta do gateway em {timeout:g} s") from error
        except openai.APIStatusError as error:
            raise self._unavailable(capability, f"gateway respondeu HTTP {error.status_code}") from error
        except openai.APIConnectionError as error:
            raise self._unavailable(capability, "gateway inacessível") from error
        return response.choices[0].message.content

    @staticmethod
    def _unavailable(capability: str, reason: str) -> ModelUnavailable:
        log.warning("capacidade %s indisponível: %s", capability, reason)
        return ModelUnavailable(capability, reason)
