# 0005. Tentativas e timeout por destino no gateway; prazo total na feature; nenhum retry no SDK

- Status: aceita
- Data: 2026-11-24
- Nível: software

## Contexto

Na v1, nenhuma chamada tinha timeout nem política de retry configurados. Valiam os padrões dos SDKs: 600 s de leitura e 2 retries. Com o provider travado, uma requisição da F1 ficou 15 min presa numa thread, e o cliente recebeu `504` da borda em 30 s (Dor 3).

No piloto da v2, com o gateway na frente, os retries se multiplicaram: o SDK da aplicação repetia o pedido inteiro ao gateway, que já repetia ao provider, e cada falha virou **9 chamadas** (Revisão 2 do plano de refatoração).

Os limites que a solução precisa respeitar:

- a borda encerra a requisição após 30 s sem bytes;
- as features síncronas precisam responder (com sucesso ou com erro explícito) em até 15 s com o provider travado (requisito 6);
- a F1 normal precisa responder em até 3 s.

## Opções consideradas

1. **Retry no SDK da aplicação.** Repete o pedido inteiro ao gateway. Multiplica as tentativas e não sabe nada de destinos.
2. **Retry e timeout só no gateway.** Ele conhece os destinos e repete por destino, mas não conhece o prazo de quem está esperando.
3. **Tentativas e timeout por destino no gateway, prazo total na feature.** Cada mecanismo fica com quem tem a informação para decidir.

## Decisão

Opção 3, com cada mecanismo num lugar declarado:

| Mecanismo | Onde | Valor |
|---|---|---|
| Timeout por tentativa (segundos sem bytes do provider) | `gateway/config.yaml`, por capacidade | F1 e F4: 5 s. F2: 30 s sem streaming, 5 s até o 1º trecho com streaming. F3: 30 s por lote |
| Retry de falha transitória (`5xx` e `429`) | `gateway/config.yaml`, `retry_policy` | 2 retries com backoff exponencial do LiteLLM: **até 3 tentativas por destino** |
| Retry de timeout | `gateway/config.yaml`, `TimeoutErrorRetries: 0` | nenhum: um provider travado não é transitório dentro do orçamento de latência, então vai direto ao próximo destino (**1 tentativa por destino**) |
| Retry de erro não transitório (`400`, `401`) | `gateway/config.yaml` | nenhum, **1 tentativa por destino**. O LiteLLM ainda aplica a lista de fallbacks a esses erros: uma entrada maior que o contexto passa pelos 3 destinos, 1 chamada em cada. Tentei desligar isso com `context_window_fallbacks`, mas o LiteLLM não reconhece o `400` do provider simulado como erro de contexto, então a configuração não teve efeito e foi removida |
| Entrada recusada por todos os destinos (`400` final do gateway) | `app/helpdesk/gateway.py` e `main.py` | o adaptador levanta `ModelRejectedInput`, e `main` responde `422`: tentar de novo não adianta |
| Ordem dos destinos | `gateway/config.yaml`, `fallbacks` | primário → fallback técnico → modelo fraco (ADR 0004) |
| Retry no SDK da aplicação | `app/helpdesk/gateway.py` | `max_retries=0`: as tentativas acontecem num lugar só |
| Prazo total que a feature espera pelo gateway | constante `TIMEOUT_SECONDS` de cada feature | F1 e F4: 14 s. F2: 28 s. F3: 60 s por lote. Estourado o prazo ou esgotados os destinos, o adaptador levanta `ModelUnavailable` e `main` responde `503` explícito |
| Cooldown (circuit breaker) | `gateway/config.yaml`, `disable_cooldowns: true` | desligado: fora do escopo. Cada requisição percorre os destinos na mesma ordem, e o comportamento fica previsível |

O prazo da F1 e da F4 (14 s) cobre o pior caso da cadeia de timeouts: 5 s no primário, mais 5 s no fallback, mais o modelo fraco. Ele também garante a resposta em até 15 s mesmo quando todos os destinos travam.

## Consequências

- Melhor: o número de tentativas é limitado, previsível e declarado (3 por destino em falha transitória, 1 em timeout). Uma falha gera 3 chamadas por destino, como na v1, e não 9. Um provider travado custa 5 s à F1, e não 15 min.
- Pior: o encadeamento até o modelo fraco é lento. Com os dois `large` em `error_500`, a F1 respondeu em 10,2 s, por causa de 6 tentativas com backoff antes do `mini`. Sem cooldown, cada requisição paga esse caminho enquanto a falha durar.
- **Limitação conhecida até o requisito 6:** a F2 ainda é síncrona. Com o primário travado, o gateway só desiste após 30 s, e a aplicação corta antes, em 28 s, com `503`. Não há `504` da borda, mas a espera é longa. O streaming do requisito 6 resolve isso com o `stream_timeout` de 5 s até o 1º trecho.

## Evidência

Medido pela borda, com `/admin/calls`:

| Cenário | Resultado | `/admin/calls` |
|---|---|---|
| `openai` em `error_500` | F1 `200` em 5,5 s | `gpt-fake-large:500` ×3 e depois `claude-fake-large:200` |
| `openai` em `timeout` | F1 `200` em 6,3 s; F4 `200` em 6,2 s | `gpt-fake-large:504` 1 vez e depois `claude-fake-large:200`. Não houve retry. O `504` é o status que o provider vai devolver ao fim dos 300 s; o `/admin/calls` o registra já no recebimento, mas o gateway desistiu em 5 s |
| Normal | F1 `200` em ~1,1 s | `gpt-fake-large:200` |
| Entrada de ~16 mil tokens (contexto de 8.000) | gateway `400`; pela borda, F1 `422` | 1 chamada `400` em cada destino (`gpt-fake-large`, `claude-fake-large`, `gpt-fake-mini`) |
