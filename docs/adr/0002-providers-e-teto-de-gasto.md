# 0002. Aceitar dois providers com redundância obrigatória e impor um teto mensal de gasto com IA

- Status: aceita
- Data: 2026-11-24
- Nível: corporativa

## Contexto

Dois fatos do cenário pedem uma regra da empresa, e não só uma escolha técnica:

1. **Indisponibilidade.** Na última instabilidade de um provider, metade do helpdesk parou junto (Dor 3). Cada feature dependia de um provider só, escolhido porque "funcionou melhor no teste".
2. **Custo.** "Três meses depois, a conta chegou." Ninguém sabia quanto a IA custava, e um relatório abandonado pela borda gastava 88% a 91% do seu custo sem entregar nada (Dor 1).

O gasto esperado, calculado com os preços do provider e os 5.000 tickets de agosto:

| Uso mensal | Custo |
|---|---|
| F1, classificação de 5.000 tickets | US$ 1,12 |
| F4, extração de 5.000 tickets | US$ 1,12 |
| F2, sugestão para 5.000 tickets | US$ 46,13 |
| F3, relatório do mês (US$ 0,849 cada), um por dia útil (22) | US$ 18,67 |
| **Total** | **~US$ 67** |

## Opções consideradas

1. **Providers.** Um só provider (simples, mas repete a Dor 3); dois providers com redundância obrigatória; vários providers à escolha de cada time.
2. **Gasto.** Nenhum teto (a situação da v1); só alerta (exige observabilidade, que está fora do escopo); teto rígido aplicado no gateway.

## Decisão

1. A empresa aceita dois providers de modelos: **OpenAI e Anthropic**. Toda capacidade de IA em produção precisa ter um destino equivalente nos dois, para que a queda de um provider não pare nenhuma feature. A regra é aplicada pelo fallback técnico (ADR 0004).
2. O helpdesk tem um **teto rígido de US$ 100 a cada 30 dias** de gasto com IA. Isso dá ~50% de folga sobre o gasto esperado de ~US$ 67. O teto é aplicado como orçamento da virtual key do app no gateway (ADR 0006). Ao atingi-lo, o gateway recusa as chamadas e as features respondem `503` explícito. Aumentar o teto é decisão da área de negócio com o financeiro, e se faz mudando o valor em `gateway/keys.yaml`.

## Consequências

- Melhor: o gasto tem dono e limite, e a indisponibilidade de um provider deixa de parar o helpdesk.
- Pior: o helpdesk mantém contrato e credencial com os dois providers. Se o teto for atingido, todas as features de IA param até o fim da janela de 30 dias ou até o teto subir, de forma deliberada e explícita.
- Passa a ser necessário revisar o teto quando o volume de tickets mudar. A F2 responde por ~70% do custo, e é onde uma troca para um modelo mais barato teria mais efeito.

## Evidência

- `gateway/config.yaml`: cada capacidade tem destino primário num provider e fallback técnico no outro.
- `gateway/keys.yaml`: `helpdesk-app` com `max_budget: 100.0` e `budget_duration: 30d`.
- O roteiro de governança do README mostra a recusa por orçamento, com uma chave de demonstração de teto pequeno.
