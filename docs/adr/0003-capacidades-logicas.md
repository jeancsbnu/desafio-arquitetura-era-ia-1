# 0003. Expor uma capacidade lógica por feature e mapear os modelos físicos só no gateway

- Status: aceita
- Data: 2026-11-24
- Nível: solução

## Contexto

Na v1, o nome do modelo físico era uma constante em `app/helpdesk/config.py`, e trocá-lo exigiu um deploy (Dor 4). Com o gateway (ADR 0001), a aplicação passa a pedir um nome lógico. É preciso decidir a granularidade desses nomes. A política de fallback com modelo fraco é decidida feature por feature (ADR 0004): a F4 não aceita o `mini`, e a F1 aceita.

## Opções consideradas

1. **Uma capacidade por nível de modelo** (`large`, `mini`). Poucas entradas, mas trocar o modelo de uma feature muda o de todas, e a política de fallback não pode ser diferente por feature.
2. **Uma capacidade por tipo de tarefa** (`json-curto`, `texto-longo`). F1 e F4 cairiam na mesma capacidade, mas têm políticas de fallback opostas.
3. **Uma capacidade por feature.** Quatro nomes, cada um com destino primário, fallback técnico e política própria.

## Decisão

Uma capacidade lógica por feature. A aplicação conhece só estes 4 nomes (constante `CAPABILITY` de cada feature):

| Capacidade lógica | Feature | Primário | Fallback técnico | Modelo fraco |
|---|---|---|---|---|
| `ticket-classification` | F1 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | `openai/gpt-fake-mini` |
| `reply-suggestion` | F2 | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` |
| `topics-report` | F3 | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` |
| `order-extraction` | F4 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | nenhum |

- **Primário:** o mesmo da v1, para não mudar o comportamento.
- **Destinos internos:** o fallback técnico e o modelo fraco são grupos internos do gateway (`<capacidade>-fallback` e `<capacidade>-weak`). A chave do app não consegue chamá-los diretamente (`403`); eles só são alcançados pelo encadeamento de fallbacks.
- **Modelos físicos:** endereço, credencial e preço de cada um são declarados uma vez em `gateway/config.yaml` (âncoras YAML). Cada destino aponta para um deles.

## Consequências

- Melhor: trocar o modelo de uma feature não afeta as outras. A política de fallback e os timeouts são por feature. A aplicação não conhece provider, modelo, preço nem parâmetros de API.
- Pior: a configuração tem 11 destinos para 4 capacidades, mais o destino interno de aquecimento (`gateway-warmup`, ADR 0008), que não é capacidade e só o bootstrap usa. As âncoras YAML evitam repetir endereço, credencial e preço, mas quem edita precisa entender a convenção.
- Uma nova feature exige uma nova capacidade no gateway e na chave do app (`gateway/keys.yaml`).

## Evidência

```sh
grep -rn "CAPABILITY = " app/helpdesk      # os 4 nomes lógicos, um por feature
curl -s localhost:4000/chat/completions -H "Authorization: Bearer sk-helpdesk-app-0001" \
  -H "Content-Type: application/json" \
  -d '{"model": "ticket-classification-weak", "messages": [{"role": "user", "content": "TASK: classify\noi"}]}'
# 403: key not allowed to access model
```
