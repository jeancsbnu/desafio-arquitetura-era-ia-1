# 0004. Fallback técnico em toda capacidade; fallback com modelo fraco decidido por feature

- Status: aceita
- Data: 2026-11-24
- Nível: solução

## Contexto

O ADR 0002 exige que nenhuma feature pare com a queda de um provider. Os dois modelos `large` são equivalentes (mesma saída para a mesma entrada), então o fallback técnico de um para o outro não muda o resultado. A pergunta difícil é outra: **sem nenhum `large` disponível, uma resposta pior é melhor que nenhuma resposta, para quem consome a feature?** Isso depende de quanto pior o `mini` é em cada tarefa e de quem consome cada feature.

A evidência foi colhida com `scripts/compare_models.py`. O script chamou o provider direto, com a mesma entrada nos dois tamanhos e em todo o dataset de agosto; o resultado está em [evidence/large-vs-mini.json](evidence/large-vs-mini.json):

| Tarefa | Entrada | `large` × `mini` | Tempo `large` → `mini` |
|---|---|---|---|
| `classify` | 5.000 tickets | **0 diferenças** | ~1,1 s → ~0,4 s |
| `topics` | os 34 lotes de 150 do mês inteiro | **0 diferenças** | ~9,8 s → ~2,7 s por lote |
| `suggest` | 200 tickets (1 a cada 25) | 200 diferentes. Texto médio de 2.431 para 1.066 caracteres, **com os mesmos 5 passos distintos** de orientação (média 5,0 nos dois); o `mini` só tem menos parágrafos de reforço | ~16 s → ~2,1 s |
| `extract` | 5.000 tickets | **1.533 errados (30,7%)**: 717 números de pedido trocados (o `mini` pega o primeiro número de 6 dígitos, que muitas vezes é a nota fiscal; ex.: `TK-00002`, nota fiscal 530720, pedido #605065, `mini` devolve `#530720`) e **953 de 953 produtos perdidos** (o `mini` nunca identifica o produto) | ~1,1 s → ~0,4 s |

## Opções consideradas

Para cada capacidade: (a) só fallback técnico e, sem `large`, falha explícita; (b) fallback técnico e, sem `large`, o `mini`.

## Decisão

Todas as capacidades têm **fallback técnico** para o `large` do outro provider. O modelo fraco é decidido por feature:

### F1 Classificar ticket: aceita o modelo fraco

O cliente espera a confirmação na tela, e a saída é curta e de valores fixos. Nos 5.000 tickets, o `mini` deu a mesma categoria e a mesma prioridade que o `large`. Não há perda de qualidade a pesar contra deixar o cliente sem resposta. Sem `large`: **`200` com a classificação do `gpt-fake-mini`**.

### F2 Sugerir resposta: aceita o modelo fraco

Quem consome é um atendente, que lê e edita o texto antes de enviar (há revisão humana). O texto do `mini` tem menos da metade do tamanho, mas cobre os mesmos 5 passos de orientação; perde só as repetições. Uma sugestão mais curta que o atendente completa é melhor que uma tela vazia. Sem `large`: **`200` com a sugestão do `claude-fake-mini`**.

### F3 Relatório de temas: aceita o modelo fraco

Ninguém está esperando, e o relatório inteiro sai igual com o `mini`: 0 diferenças nos 34 lotes do mês. Ele ainda sai mais barato e mais rápido. Sem `large`: **o relatório é produzido com o `claude-fake-mini`**.

### F4 Extrair dados do pedido: não aceita o modelo fraco

A saída alimenta uma automação de troca e devolução **sem revisão humana**. O `mini` erra 30,7% das extrações: em 14,3% dos tickets ele troca o número do pedido pelo da nota fiscal, e perde o produto em todos os tickets que citam um. Cada erro vira uma troca errada, que custa frete, estoque e cliente, e ninguém revisa antes. Uma falha explícita deixa o ticket para tratamento posterior. Sem `large`: **`503` com `{"detail", "capability": "order-extraction", "reason"}`**, sem chamar o `mini`. O gateway nem tem um destino fraco para essa capacidade.

## Consequências

- Melhor: com um provider fora, nenhuma feature para e nenhuma muda de resultado. Com os dois `large` fora, três features seguem funcionando, e a F4 falha de forma explícita em vez de responder errado.
- Pior: a F1, a F2 e a F3 podem responder com o `mini` sem avisar o cliente da API, porque o contrato das features é fixo. A troca só aparece em `/admin/calls` e nos logs do gateway.
- Para chegar ao modelo fraco, o gateway esgota antes as tentativas nos dois `large` (ADR 0005). Por isso esse caminho é mais lento: ~10 a 13 s nas medições abaixo.
- Se a qualidade do `mini` mudar (outro modelo, outra versão), esta decisão precisa ser reavaliada com o mesmo script.

## Evidência

O comportamento implementado foi conferido pela borda, com `/admin/calls`.

**Provider primário inteiro em `error_500`** (`{"provider": "openai", "mode": "error_500"}`). F1 e F4 respondem `200`, e o `/admin/calls` registra `gpt-fake-large:500` 3 vezes e depois `claude-fake-large:200`. Com o `anthropic` fora, o mesmo acontece com F2 e F3, invertendo os providers.

**Os dois `large` em `error_500`** (`{"provider": "openai", "model": "gpt-fake-large", "mode": "error_500"}` e `{"provider": "anthropic", "model": "claude-fake-large", "mode": "error_500"}`):

| Feature | Resposta | `/admin/calls` |
|---|---|---|
| F1 | `200` em 10,2 s | 3× `gpt-fake-large:500`, 3× `claude-fake-large:500`, `gpt-fake-mini:200` |
| F2 | `200` em 12,0 s | 3× `claude-fake-large:500`, 3× `gpt-fake-large:500`, `claude-fake-mini:200` |
| F3 (1 dia) | `200` em 12,8 s | 3× `claude-fake-large:500`, 3× `gpt-fake-large:500`, `claude-fake-mini:200` |
| F4 | `503` em 9,5 s | 3× `gpt-fake-large:500`, 3× `claude-fake-large:500`, nenhuma chamada ao `mini` |
