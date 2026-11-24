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
- Nesta etapa a aplicação usa a master key do gateway, porque o LiteLLM só emite virtual keys com banco de dados. **É provisório:** uma credencial de administração não deveria estar no serviço que só chama capacidades. No requisito 5, o `app` passa a usar uma virtual key própria, e a master key fica só com quem administra o gateway. Virtual keys, orçamento, limites, timeout, retry e fallback entram depois da tag (requisito 5).

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

**O que a mudança ganha além do número.** A mudança foi motivada pela métrica, e isso fica dito aqui. O que a justifica é o que ela entrega à estrutura:

- **A seta fica escrita no código.** Com a implementação só estrutural, a dependência do adaptador em relação à porta existia, mas não estava em lugar nenhum. Agora ela está, no sentido que a inversão de dependência pede: o detalhe depende da abstração. Dá para responder "quem implementa esta porta?" lendo o código.
- **A porta passa a exigir a implementação.** Este ponto foi corrigido depois da revisão independente (ver "Revisão independente da escala"). A primeira versão afirmava que declarar a porta "verificava a conformidade", e isso era falso: em Python, `class X(Porta)` sozinha não verifica nada, e um adaptador que esquecesse `complete` herdaria o corpo vazio da porta e devolveria `None`. Os métodos das portas passaram a ser `@abstractmethod` (o `Protocol` já usa `ABCMeta`). Agora, um adaptador que declara a porta e não implementa um método levanta `TypeError` ao ser instanciado em `main`, e a aplicação não sobe. Isso vai importar quando a porta crescer, com o streaming do requisito 6.

**O que o número diz e o que não diz.** O `gateway` e o `tickets` ficaram com I 0,50 e D 0,50: fora da zona de dor, mas exatamente na fronteira, porque a zona exige I < 0,5. Qualquer adaptador com um dependente (`main`) e uma dependência (a porta) cai nesse ponto. A melhora de D (de 1,00 para 0,50) não prova sozinha que o acoplamento caiu. O que caiu de fato é que nenhuma feature depende mais de um componente concreto.

### Revisão 2: retry num lugar só

**O que a medição mostrou.** O gateway já faz 3 tentativas por conta própria (1 + 2 retries): com o `openai` em `error_500`, uma chamada direta ao gateway gerou 3 chamadas ao provider em 3,9 s. O SDK da aplicação, com o padrão de 2 retries, repete o pedido inteiro ao gateway. Medido pela borda, com o adaptador usando o padrão do SDK, a F1 respondeu `500` em 15,9 s e gerou **9 chamadas** ao provider. Na v1 eram 3.

**O que mudou no plano.** O adaptador cria o cliente com `max_retries=0`, e as tentativas acontecem num lugar só, o gateway. Medido depois da mudança: `500` em 4,8 s e **3 chamadas**, o mesmo número da v1. Isso entra no critério de pronto (item 6 abaixo). Depois da revisão independente, o total de tentativas do gateway passou a ser declarado em `gateway/config.yaml` (`router_settings.num_retries: 2`), em vez de depender do padrão da versão da imagem; medido de novo, continuam 3 chamadas por falha. Timeout explícito, backoff e fallback continuam no requisito 5, depois da tag, e esta decisão será registrada no ADR de resiliência.

**Como reproduzir a comparação.** O adaptador com os retries do SDK não existe mais no repositório. Para refazer a medição, parta do commit do piloto (`8d28f1e`) e tire `max_retries=0` de `app/helpdesk/gateway.py`. Depois rode `docker compose up -d --build --wait app`, coloque o `openai` em `error_500` (`POST /admin/failures {"provider": "openai", "mode": "error_500"}`), chame a F1 pela borda e conte as chamadas em `/admin/calls`. Devolva `max_retries=0` e repita para comparar.

### Revisão 3: parâmetros do provider ficam no gateway

**O que a revisão independente mostrou.** A v1 só enviava `max_tokens` ao `anthropic`, porque o formato Messages o exige (`git show v1-coupled:app/helpdesk/llm.py`). O adaptador do piloto passou a enviar `max_tokens=2000` em todas as capacidades, inclusive F1 e F4. O provider simulado trunca a saída com `max_tokens`. A suíte não mudou porque as saídas da F1 e da F4 têm 11 tokens, mas era uma mudança de comportamento não declarada. E era também um detalhe de provider vazando para a aplicação.

**O que mudou.** O `max_tokens` saiu da aplicação: nem `main` nem o adaptador o conhecem mais. Ele foi para `gateway/config.yaml`, só nas capacidades atendidas pelo `anthropic` (`max_tokens: 2000`). As requisições voltaram a chegar ao provider como na v1. Para confirmar que o gateway repassa o parâmetro configurado, coloquei temporariamente `max_tokens: 50` na `reply-suggestion`: a resposta veio com `finish_reason: length` e o `/admin/calls` registrou 50 tokens de saída. Depois voltei para 2000.

### Critério de pronto revisado

Os itens 1 a 5 continuam valendo. Os itens 3 e 6 abaixo substituem ou completam o critério original:

3. **Direção das dependências (revisado):** o SDK é importado por um único componente (`gateway`), e só `main` importa esse componente. Nenhuma feature importa um adaptador: o Ce de cada feature está contido em {`ports`, `schemas`}. **Cada adaptador importa a porta que implementa.**
6. **Tentativas (novo):** com o provider de uma capacidade em `error_500`, `/admin/calls` mostra no máximo 3 chamadas por requisição, o mesmo que a v1.

## Handoff para o agente

**Divisão do trabalho.** O plano, o piloto e as revisões foram feitos com o Claude Code (Claude Opus), conduzido por mim. A escala (F2, F3, F4, `tickets` e a remoção de `llm` e `config`) foi entregue a outro agente, um subagente Claude Sonnet sem acesso a esta conversa. Ele recebeu só o brief e o repositório. Assim, quem julga o resultado não é quem o produziu.

**O que foi entregue ao agente.** O brief completo está em [handoff-brief.md](handoff-brief.md), com o texto exatamente como foi enviado. Ele continha:

- o que ler (este plano já com as revisões, e o piloto como padrão a copiar);
- as 7 mudanças, uma por componente, com o nome lógico de cada capacidade;
- as restrições: comportamento externo igual, nada de mexer em `tests/`, `gateway/` e `metrics/`, nenhum nome físico nem chave em `app/`, Ce das features ⊆ {`ports`, `schemas`}, nenhuma dependência ou tratamento de erro novo;
- a verificação que ele devia rodar e relatar.

O brief não continha a métrica esperada nem as conclusões do piloto além do que está no plano.

**O que voltou.** O agente relatou as mudanças, 30 testes passando e a tabela da métrica, sem desvio de design. Ele avisou de um incidente operacional: um pytest rodado em segundo plano pelo Bash travou, e ele o encerrou e repetiu a execução em primeiro plano. Avisou também do que não verificou: a comparação de `/admin/calls` com a v1 e o cenário de `error_500`.

**Como o resultado foi julgado.** O relato do agente não foi usado como prova. Repeti cada item do critério de pronto revisado:

| Critério | Verificação independente | Resultado |
|---|---|---|
| 1. Comportamento | rebuild + `python -m pytest tests/characterization`; `git diff v1-coupled -- tests/characterization/` | 30 passaram; suíte sem diferença |
| 2. Métrica | `python metrics/coupling.py app/helpdesk ...` | só `schemas` na zona de dor (ver tabela abaixo) |
| 3. Direção das dependências | leitura do diff; `grep` por imports de SDK e de adaptadores | `openai` importado só em `gateway`; `gateway` e `tickets` importados só por `main`; cada adaptador declara sua porta; Ce de cada feature = {`ports`, `schemas`} |
| 4. Gateway | `grep` por nomes físicos e chaves em `app/`; `docker compose exec app env`; `docker compose config` | greps vazios; o `app` recebe só `GATEWAY_URL` e `GATEWAY_API_KEY`, sem `env_file` |
| 5. Prova no provider | `/admin/calls` depois da suíte | `classify` e `extract` em `openai/gpt-fake-large`, `suggest` e `topics` em `anthropic/claude-fake-large`, como na v1 |
| 6. Tentativas | cada provider em `error_500`, uma requisição por feature | 3 chamadas por requisição nas 4 features, `500` em ~4,7 s |

Li também o diff das 4 features contra a v1. Prompts, validações (`422` e `502`), lotes de 150, soma por tema, corte em 3 exemplos e ordenação ficaram iguais. Nas features, a única mudança é de onde vêm o modelo e os tickets. Uma diferença que estava no adaptador do piloto, e não nas features, passou nesta leitura e foi apontada pela revisão independente: o `max_tokens` (Revisão 3).

**Aceito ou rejeitado.** Nesta rodada, nada do que o agente produziu precisou ser rejeitado. A rejeição que houve no processo foi do próprio piloto: a métrica reprovou o adaptador implementado só estruturalmente (Revisão 1), e a contagem de chamadas no `/admin/calls` reprovou os retries do SDK (Revisão 2). As duas correções entraram no plano antes do handoff, e por isso o agente já recebeu o padrão corrigido.

## Revisão independente da escala

Antes da tag, um terceiro agente (um subagente Claude Sonnet, sem acesso às conclusões anteriores) revisou o requisito 4 inteiro. Ele recebeu o enunciado, este plano, o brief, o código e as medições. Não encontrou nada que bloqueasse a tag e confirmou por conta própria:

- `provider-fake/`, `edge/` e `data/` iguais ao commit base;
- a suíte idêntica à da `v1-coupled`;
- os greps vazios e o ambiente do `app` sem chaves de provider;
- os modelos em `/admin/calls`;
- o CSV da v2 reproduzido byte a byte e recalculado à mão em 5 componentes.

O que ele apontou e o que foi feito:

| Apontamento | O que foi feito |
|---|---|
| A Revisão 1 afirmava que declarar a porta verificava a conformidade, o que é falso em Python | Métodos das portas com `@abstractmethod`. A justificativa foi reescrita, assumindo que a mudança foi motivada pela métrica e que o adaptador fica na fronteira (I 0,50) |
| O adaptador enviava `max_tokens` também para F1 e F4, sem declarar | `max_tokens` foi para a configuração do gateway, só nas capacidades do `anthropic` (Revisão 3) |
| As 3 tentativas dependiam de um padrão não escrito do LiteLLM | `router_settings.num_retries: 2` em `gateway/config.yaml` (Revisão 2) |
| Os números de 9 e 3 chamadas não eram reproduzíveis a partir do repositório | O roteiro de reprodução foi registrado (Revisão 2) |
| `handoff-brief.md` expunha um caminho local da máquina | Trocado por um marcador, com aviso no topo do brief |
| `app` dependia de `provider-fake` no compose sem falar mais com ele | `depends_on` só no `gateway` |
| Master key no `app` sem aviso de que é provisório | Aviso no plano e no `compose.yaml` |
| O ADR da métrica que vai declarar `schemas` ainda não existe | Pendência registrada no README. É critério da `main`, não da tag |

Depois das correções: a suíte passou de novo (30), a métrica reproduziu o CSV e o SVG da v2 byte a byte (as correções não mudam nenhum import nem nenhuma classe), e a contagem de tentativas com cada provider em `error_500` continuou em 3.

## Resultado: v2-decoupled

Medição com o mesmo script ([CSV](../metrics/results/v2-decoupled.csv), [gráfico](../metrics/results/v2-decoupled.svg)):

| Componente | Ca | Ce | I | A | D | Zona de dor |
|---|---|---|---|---|---|---|
| `classification` | 1 | 2 | 0,67 | 0,00 | 0,33 | |
| `extraction` | 1 | 2 | 0,67 | 0,00 | 0,33 | |
| `gateway` | 1 | 1 | 0,50 | 0,00 | 0,50 | |
| `main` | 0 | 7 | 1,00 | 0,00 | 0,00 | |
| `ports` | 6 | 0 | 0,00 | 1,00 | 0,00 | |
| `report` | 1 | 2 | 0,67 | 0,00 | 0,33 | |
| `schemas` | 5 | 0 | 0,00 | 0,00 | 1,00 | sim, estável por natureza (a declarar no ADR da métrica) |
| `suggestion` | 1 | 2 | 0,67 | 0,00 | 0,33 | |
| `tickets` | 1 | 1 | 0,50 | 0,00 | 0,50 | |

Comparado com a v1: `llm` (D 0,80) e `config` (D 1,00) deixaram de existir. O componente mais dependido agora é `ports` (Ca 6), que é abstrato e está sobre a Main Sequence. As features saíram de I 0,75–0,80 com dependências concretas para I 0,67 com dependências só da porta e dos tipos do contrato.
