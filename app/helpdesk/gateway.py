"""Adaptador da porta `LanguageModel` para o AI Gateway.

É o único componente da aplicação que conhece um SDK de modelo. O gateway expõe a API no formato
da OpenAI para todas as capacidades, qualquer que seja o provider por trás de cada uma.
"""

from openai import OpenAI

from .ports import LanguageModel


class GatewayLanguageModel(LanguageModel):
    """Declara a porta que implementa: o detalhe depende da abstração, e a conformidade com a
    porta é verificada aqui, na definição do adaptador."""

    def __init__(self, base_url: str, api_key: str, max_output_tokens: int):
        # Sem retry no SDK: as tentativas acontecem num lugar só, o gateway.
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self._max_output_tokens = max_output_tokens

    def complete(self, capability: str, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=capability,
            max_tokens=self._max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
