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

**Medição antes e depois:** a preencher após o piloto.

## Revisões

_A preencher após o piloto._

## Handoff para o agente

_A preencher na escala da refatoração._
