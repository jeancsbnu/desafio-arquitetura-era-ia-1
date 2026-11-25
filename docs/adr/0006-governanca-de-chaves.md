# 0006. Virtual keys por consumidor, com orçamento e limite declarados em arquivo e aplicados na subida

- Status: aceita
- Data: 2026-11-24
- Nível: solução

## Contexto

Na v1 a aplicação tinha as chaves dos providers e não havia controle de gasto nem de volume. Na `v2-decoupled`, como passo provisório, o `app` usou a master key do gateway, que é uma credencial de administração. O ADR 0002 fixa um teto de gasto mensal, e o enunciado pede pelo menos uma chave com orçamento e uma com limite de requisições, com a recusa acontecendo no gateway, antes do provider. As chaves precisam ser criadas sem passo manual.

## Opções consideradas

1. **Master key para todos.** Não existe orçamento nem limite por consumidor, e uma credencial de administração fica espalhada.
2. **Virtual keys criadas à mão** pela API ou pela interface do LiteLLM. Atende à governança, mas exige um passo manual a cada ambiente novo.
3. **Virtual keys declaradas em arquivo versionado** e aplicadas pelo próprio serviço `gateway` na subida.

## Decisão

Opção 3:

- **Declaração:** `gateway/keys.yaml` declara as chaves. O valor de cada uma vem do `.env`, e o orçamento é em US$.
- **Aplicação na subida:** `gateway/start.sh` sobe o LiteLLM e roda `gateway/bootstrap_keys.py` em paralelo. O script espera o gateway com banco conectado, cria cada chave que não existe e atualiza os limites das que já existem. No fim, marca `/tmp/keys-ready`.
- **Healthcheck:** o do `gateway` exige essa marca, então o `app` (`depends_on: service_healthy`) só sobe com as chaves aplicadas.
- **Master key:** fica só no serviço `gateway`, para o bootstrap e para administração.

| Chave (alias) | Quem usa | Capacidades | Orçamento | Limite de requisições |
|---|---|---|---|---|
| `helpdesk-app` | o serviço `app` | as 4 capacidades lógicas, e só elas | US$ 100 / 30 dias (ADR 0002) | 600 por minuto: proteção contra laços e abusos, muito acima do volume do helpdesk (~5.000 tickets por mês) |
| `demo-budget` | o roteiro de governança do README | `ticket-classification` | US$ 0,0001 / 30 dias: uma classificação (US$ 0,00014) já estoura | não tem |
| `demo-rate-limit` | o roteiro de governança do README | `ticket-classification` | não tem | 2 por minuto |

As chaves de demonstração existem para que a recusa seja reproduzível em segundos, sem esgotar o orçamento real do app. O custo é calculado com o preço de cada modelo declarado em `gateway/config.yaml`; sem isso, o LiteLLM não conhece os modelos simulados e calcula custo zero.

## Consequências

- Melhor: cada consumidor tem credencial, orçamento e limite próprios, e um arquivo versionado diz quem pode o quê. O `app` não tem mais nenhuma credencial de administração. Uma requisição recusada não chega ao provider e não custa nada.
- Pior: o gasto acumulado fica no banco do gateway (ADR 0007). Se o banco for recriado (`docker compose down`), as chaves são recriadas pelo bootstrap, mas o gasto acumulado da janela volta a zero.
- O LiteLLM recusa por orçamento com `429` (`budget_exceeded`) e por limite também com `429`. Na chave do app, as duas recusas viram `503` explícito na feature (`ModelUnavailable`, ADR 0005).

## Evidência

Roteiro de governança do README, executado com o provider em modo normal:

| Chave | Requisições | Resultado | `/admin/calls` |
|---|---|---|---|
| `demo-budget` | 3 | 1ª `200` (custo de US$ 0,000142); 2ª e 3ª `429` com "Budget has been exceeded" | 1 chamada |
| `demo-rate-limit` | 4 no mesmo minuto | 1ª e 2ª `200`; 3ª e 4ª `429` com "Rate limit exceeded" | 2 chamadas |
