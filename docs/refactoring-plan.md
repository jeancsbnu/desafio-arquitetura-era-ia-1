# Plano de refatoração: da v1-coupled à v2-decoupled

Este plano foi escrito antes do piloto. O que o piloto e as medições mudaram está em [Revisões](#revisões), sem apagar o plano original.

## Ponto de partida

Medição da `v1-coupled` com `metrics/coupling.py` ([CSV](../metrics/results/v1-coupled.csv), [gráfico](../metrics/results/v1-coupled.svg)):

| Componente | Ca | Ce | I | A | D | Zona de dor |
|---|---|---|---|---|---|---|
| `classification` | 1 | 3 | 0,75 | 0,00 | 0,25 | |
| `config` | 6 | 0 | 0,00 | 0,00 | 1,00 | sim |
| `extraction` | 1 | 3 | 0,75 | 0,00 | 0,25 | |
| `llm` | 4 | 1 | 0,20 | 0,00 | 0,80 | sim |
| `main` | 0 | 5 | 1,00 | 0,00 | 0,00 | |
| `report` | 1 | 4 | 0,80 | 0,00 | 0,20 | |
| `schemas` | 5 | 0 | 0,00 | 0,00 | 1,00 | sim |
| `suggestion` | 1 | 3 | 0,75 | 0,00 | 0,25 | |
| `tickets` | 1 | 1 | 0,50 | 0,00 | 0,50 | |

Nenhum componente é abstrato. Os três da zona de dor são os mais importados, e dois deles (`llm` e `config`) concentram justamente o que muda: SDK, formato de API, credencial e nome do modelo físico (Dores 3 e 4 do diagnóstico).

## Objetivo da v2

A aplicação passa a pedir uma **capacidade** a uma abstração, e quem decide o modelo físico é o gateway. O comportamento externo não muda: mesmas rotas, mesmos modos de resposta (todos síncronos), suíte de caracterização passando sem alteração, e um modelo `large` por trás de cada capacidade.

## Componentes-alvo e estratégia

| Componente da v1 | O que a medição mostra | Estratégia | Resultado esperado |
|---|---|---|---|
| `llm` | Zona de dor (D 0,80), Ca 4: as features dependem dele, e ele depende de dois SDKs | **Inverter a dependência.** Uma porta `LanguageModel` (`typing.Protocol`) em `ports` declara o que as features precisam: gerar texto para uma capacidade. O `llm` é substituído por um adaptador `gateway`, o único componente que conhece um SDK, falando com o gateway pelo nome lógico | `ports` abstrato (A = 1), muito dependido e sem dependências: canto estável-abstrato, sobre a Main Sequence. `gateway` dependido só por `main` |
| `config` | Zona de dor (D 1,00), o mais importado (Ca 6), e mistura credenciais, URLs e nomes de modelo | **Reduzir Ca a zero, eliminando o módulo compartilhado.** Nomes de modelo e chaves de provider saem da aplicação e vão para o gateway. O que sobra (URL e chave do gateway, caminho dos tickets, limite de tokens) é lido no ponto de composição e passado por construtor | O componente deixa de existir |
| `tickets` | Fronteira da zona (I 0,50, D 0,50), lê `config` | **Inverter a dependência.** Porta `TicketSource` em `ports`; `tickets` vira o adaptador do arquivo JSONL e recebe o caminho por construtor | `report` depende da porta, não do arquivo |
| `classification`, `extraction`, `suggestion`, `report` | Dependem de `llm` e `config` concretos | **Compor em vez de importar módulos globais.** Cada feature recebe as portas de que precisa (injeção pelo construtor) e conhece só o nome lógico da sua capacidade | Ce de cada feature ⊆ {`ports`, `schemas`} |
| `schemas` | Zona de dor (D 1,00), Ca 5 | **Não mexer.** São os tipos de valor do contrato HTTP (modelos pydantic), concretos por natureza, e só mudam quando o contrato muda. Criar abstrações para DTOs só para subir A seria desenhar o código pela fórmula | Declarado "estável por natureza" no ADR da métrica |
| `main` | Ponto de entrada (I 1,00) | **Ponto de composição.** Lê o ambiente, monta os adaptadores e entrega cada um às features. É o único componente que conhece os adaptadores | I 1,00, D 0,00 |

Nenhuma herança nova: os adaptadores implementam as portas e são entregues às features por composição.

## Gateway (mínimo da v2)

- LiteLLM Proxy, imagem `ghcr.io/berriai/litellm:v1.102.1` (a versão testada no README do `provider-fake`), como serviço `gateway` no compose, com a configuração em `gateway/config.yaml`.
- Uma capacidade lógica por feature, porque a política de fallback com modelo fraco (requisito 5) é decidida feature por feature:

  | Capacidade lógica | Feature | Modelo físico na v2 (o mesmo da v1) |
  |---|---|---|
  | `ticket-classification` | F1 | `openai/gpt-fake-large` |
  | `reply-suggestion` | F2 | `anthropic/claude-fake-large` |
  | `topics-report` | F3 | `anthropic/claude-fake-large` |
  | `order-extraction` | F4 | `openai/gpt-fake-large` |

- As chaves dos providers vivem só no serviço `gateway`. O serviço `app` deixa de receber o `.env` e recebe só a URL e a chave do gateway.
- Nesta etapa a aplicação usa a master key do gateway, porque o LiteLLM só emite virtual keys com banco de dados. Virtual keys, orçamento, limites, timeout, retry e fallback entram depois da tag (requisito 5).

## Critério de pronto

A refatoração está pronta quando todos os itens abaixo forem verdadeiros. Os testes e a métrica são o juiz: o que o agente produzir só é aceito se passar por eles.

1. **Comportamento:** `python -m pytest tests/characterization` passa, e `git diff v1-coupled -- tests/characterization/` não mostra diferença.
2. **Métrica:** nenhum componente na zona de dor (A < 0,5, I < 0,5 e D ≥ 0,5), exceto `schemas`. O componente que chama o gateway e os componentes das features não podem estar lá.
3. **Direção das dependências:** o SDK é importado por um único componente (`gateway`), e só `main` importa esse componente. Nenhuma feature importa um adaptador: o Ce de cada feature está contido em {`ports`, `schemas`}.
4. **Gateway:** `grep -rE "gpt-fake|claude-fake" app/` e `grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/` não retornam nada, e o serviço `app` em `docker compose config` não recebe as chaves dos providers.
5. **Prova no provider:** depois da suíte, `/admin/calls` mostra cada tarefa no mesmo modelo `large` da v1.

## Piloto

**Componente:** `classification` (F1). É a feature menor (síncrona, saída curta) e tem a mesma forma das outras três (ticket entra, resultado sai). O piloto exercita os três elementos novos de uma vez: a porta `LanguageModel`, o adaptador `gateway` e a composição em `main`. As outras features continuam na v1 até o piloto ser aceito.

**O que se espera medir:** o Ce de `classification` passa de {`config`, `llm`, `schemas`} para {`ports`, `schemas`}; o Ca de `config` cai de 6 para 5 e o de `llm` de 4 para 3; surgem `ports` (A = 1) e `gateway`. A suíte continua passando, agora com a F1 indo pelo gateway.

**Medição antes e depois.** Os componentes que o piloto tocou, medidos com o mesmo script: antes, a `v1-coupled`; depois, o piloto como foi escrito ([CSV](../metrics/results/pilot-classification.csv), [gráfico](../metrics/results/pilot-classification.svg)); e depois da Revisão 1 ([CSV](../metrics/results/pilot-classification-revised.csv), [gráfico](../metrics/results/pilot-classification-revised.svg)).

| Componente | v1-coupled (Ca / Ce / I / A / D) | Piloto como escrito | Piloto revisado |
|---|---|---|---|
| `classification` | 1 / 3 / 0,75 / 0,00 / 0,25 | 1 / 2 / 0,67 / 0,00 / 0,33 | 1 / 2 / 0,67 / 0,00 / 0,33 |
| `ports` | não existia | 1 / 0 / 0,00 / 1,00 / 0,00 | 2 / 0 / 0,00 / 1,00 / 0,00 |
| `gateway` | não existia | 1 / 0 / 0,00 / 0,00 / **1,00, zona de dor** | 1 / 1 / 0,50 / 0,00 / 0,50 |
| `llm` | 4 / 1 / 0,20 / 0,00 / 0,80 | 3 / 1 / 0,25 / 0,00 / 0,75 | 3 / 1 / 0,25 / 0,00 / 0,75 |
| `config` | 6 / 0 / 0,00 / 0,00 / 1,00 | 5 / 0 / 0,00 / 0,00 / 1,00 | 5 / 0 / 0,00 / 0,00 / 1,00 |
| `main` | 0 / 5 / 1,00 / 0,00 / 0,00 | 0 / 6 / 1,00 / 0,00 / 0,00 | 0 / 6 / 1,00 / 0,00 / 0,00 |

A suíte de caracterização passou inteira (30 testes) com a F1 indo pelo gateway. Em `/admin/calls`, as chamadas `classify` continuaram em `openai/gpt-fake-large`, e o log do gateway registrou as requisições vindas do `app`.

O que saiu como esperado: o Ce de `classification` virou {`ports`, `schemas`}, `ports` nasceu sobre a Main Sequence (A 1,00, D 0,00), e o Ca de `llm` e de `config` caiu de 1. O que não estava previsto está nas revisões abaixo.

## Revisões

### Revisão 1: o adaptador declara a porta que implementa

**O que a medição mostrou.** No piloto como escrito, o `gateway` caiu na zona de dor: Ca 1 (`main`), Ce 0, I 0,00, A 0,00, D 1,00. O adaptador implementava a porta só estruturalmente, como o `typing.Protocol` permite, sem importá-la. Para a métrica, ele parecia um componente concreto, estável e de quem outro depende. E é o componente que chama o gateway, que a régua não aceita como exceção.

**O que mudou no plano.** O plano dizia "nenhuma herança nova". Os adaptadores passam a declarar a porta que implementam (`class GatewayLanguageModel(LanguageModel)`). Isso não herda comportamento: a porta não tem implementação, e o comportamento continua vindo por composição.

**Por que a estrutura melhorou, e não só o número.** Com a implementação só estrutural, a dependência do adaptador em relação à porta existia, mas não estava escrita em lugar nenhum. A seta passa a estar no código, no sentido que a inversão de dependência pede: o detalhe depende da abstração. A conformidade passa a ser verificada na definição do adaptador, e não só no ponto em que `main` o injeta. Isso vai importar quando a porta crescer (o streaming do requisito 6). A métrica agora lê o adaptador como ele é: um detalhe que depende da abstração (I 0,50, fora da zona de dor). O mesmo vale para o adaptador de tickets que vem na escala.

### Revisão 2: retry num lugar só

**O que a medição mostrou.** O gateway já faz 3 tentativas por conta própria (1 + 2 retries): com o `openai` em `error_500`, uma chamada direta ao gateway gerou 3 chamadas ao provider em 3,9 s. O SDK da aplicação, com o padrão de 2 retries, repete o pedido inteiro ao gateway. Medido pela borda, com o adaptador usando o padrão do SDK, a F1 respondeu `500` em 15,9 s e gerou **9 chamadas** ao provider. Na v1 eram 3.

**O que mudou no plano.** O adaptador cria o cliente com `max_retries=0`, e as tentativas acontecem num lugar só, o gateway. Medido depois da mudança: `500` em 4,8 s e **3 chamadas**, o mesmo número da v1. Isso entra no critério de pronto (item 6 abaixo). Timeout explícito, backoff e fallback continuam no requisito 5, depois da tag, e esta decisão será registrada no ADR de resiliência.

### Critério de pronto revisado

Os itens 1 a 5 continuam valendo. Os itens 3 e 6 abaixo substituem ou completam o critério original:

3. **Direção das dependências (revisado):** o SDK é importado por um único componente (`gateway`), e só `main` importa esse componente. Nenhuma feature importa um adaptador: o Ce de cada feature está contido em {`ports`, `schemas`}. **Cada adaptador importa a porta que implementa.**
6. **Tentativas (novo):** com o provider de uma capacidade em `error_500`, `/admin/calls` mostra no máximo 3 chamadas por requisição, o mesmo que a v1.

## Handoff para o agente

_A preencher na escala da refatoração._
