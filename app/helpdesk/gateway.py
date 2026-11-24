"""Adaptador da porta `LanguageModel` para o AI Gateway.

É o único componente da aplicação que conhece um SDK de modelo. O gateway expõe a API no formato
da OpenAI para todas as capacidades, qualquer que seja o provider por trás de cada uma. Parâmetros
que dependem do provider (como o `max_tokens` obrigatório no formato Messages) ficam na
configuração do gateway, não aqui.
"""

from openai import OpenAI

from .ports import LanguageModel


class GatewayLanguageModel(LanguageModel):
    """Declara a porta que implementa: o detalhe depende da abstração, e se faltar um método da
    porta o adaptador não pode ser instanciado."""

    def __init__(self, base_url: str, api_key: str):
        # Sem retry no SDK: as tentativas acontecem num lugar só, o gateway.
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def complete(self, capability: str, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=capability,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
