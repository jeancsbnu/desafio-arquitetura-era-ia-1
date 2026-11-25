# 0009. Ler a métrica de acoplamento com a régua fixa e declarar `schemas` como estável por natureza

- Status: aceita
- Data: 2026-11-25
- Nível: software

## Contexto

O requisito 3 fixa a régua: componente é cada módulo de `app/helpdesk/`, dependência é o import que resolve para outro componente, e Ca, Ce, I, A e D seguem as fórmulas da Main Sequence. A régua não pode ser ajustada ao resultado. Mas alguns componentes concretos que quase nunca mudam caem na zona de dor pela fórmula sem serem um problema real. O enunciado pede que eles sejam declarados aqui, com justificativa, e proíbe que o componente que chama o gateway ou os das features sejam exceção.

As três medições, feitas com `metrics/coupling.py` ([v1-coupled](../../metrics/results/v1-coupled.csv), [v2-decoupled](../../metrics/results/v2-decoupled.csv), [main](../../metrics/results/main.csv)), mostram a zona de dor assim:

| Versão | Na zona de dor (A < 0,5, I < 0,5, D ≥ 0,5) |
|---|---|
| `v1-coupled` | `config` (D 1,00), `llm` (D 0,80), `schemas` (D 1,00) |
| `v2-decoupled` | `schemas` (D 1,00) |
| `main` | `schemas` (D 1,00) |

## Opções consideradas

1. **Abstrair `schemas`** (`Protocol` para os tipos de entrada e saída) para subir A. Seria desenhar o código pela fórmula: os tipos do contrato HTTP são dados, não comportamento, e não têm implementações alternativas.
2. **Espalhar os tipos pelas features**, cada uma com os seus, para reduzir o Ca de `schemas`. Duplicaria tipos compartilhados (`TicketInput` é usado por F1, F2 e F4) e trocaria um componente estável por várias cópias que precisam mudar juntas.
3. **Declarar `schemas` como estável por natureza** e manter a régua intacta para todos os outros componentes.

## Decisão

Opção 3. **`schemas` é declarado estável por natureza.**

**Justificativa.** O módulo contém só os tipos de valor (modelos pydantic sem comportamento) do contrato HTTP das 4 features, mais o estado de uma tarefa da F3. As rotas, os campos e os valores permitidos desse contrato são fixos pelo enunciado ("As quatro features são fixas no que recebem e no resultado que produzem"). O módulo muda quando o contrato muda, e uma mudança de contrato atinge todos os consumidores, com ou sem abstração. Ser concreto e muito dependido é o esperado para tipos de valor.

Nenhum outro componente é exceção. Em particular, o `gateway` (o componente que chama o gateway) e as quatro features estão fora da zona de dor.

Decisões de interpretação da régua, que valem para as três medições e estão no docstring do script:
- conta qualquer import, inclusive dentro de funções;
- um import que resolve para um pacote (`__init__.py`) não conta, porque o pacote não é componente;
- uma classe é abstrata quando `typing.Protocol` ou `abc.ABC` está entre as suas bases diretas;
- os valores são arredondados com meio para cima, sobre frações exatas.

## Consequências

**Leitura das três medições:**

- **v1 → v2.**
  - `llm` e `config` deixaram de existir. O nome do modelo, a credencial e os parâmetros de provider foram para o gateway (ADR 0001 e ADR 0003), e a configuração restante é lida no ponto de composição.
  - O componente mais dependido passou a ser `ports`, abstrato.
  - As features trocaram dependências concretas (`llm` e `config`) por `ports` e `schemas`.
- **v2 → main.**
  - `ports` ganhou o streaming, a porta `TaskRunner` e as exceções do contrato. Com 3 `Protocol`s e 2 exceções (A 0,60), e passando a importar `schemas` para o tipo `TaskStatus` (I 0,11), fica com D 0,29, perto da Main Sequence.
  - Nasceu o adaptador `background` (I 0,67, D 0,33), dependido só por `main`.

**Componentes na fronteira, sem estarem na zona:**
- `gateway` e `tickets` têm I exatamente 0,50 e D 0,50, por serem adaptadores com um dependente (`main`) e uma dependência (a porta).
- A Revisão 1 do plano de refatoração registra que declarar a porta foi motivado pela métrica, e o que isso ganhou além do número.
- Um adaptador novo nesse formato também cai na fronteira. Ela deve ser lida como "detalhe que depende da abstração", não como alvo a otimizar.
- Eles só ficam fora da zona porque a régua usa desigualdade estrita (I < 0,5). Quem lesse com I ≤ 0,5 os encontraria na zona de dor, inclusive o componente que chama o gateway.

A defesa da estrutura não depende do número. Os adaptadores são concretos e voláteis por natureza (mudam quando o SDK, o gateway ou o arquivo mudam). Isso é aceitável porque:
- só o ponto de composição depende deles, e nenhuma feature;
- eles dependem da abstração que implementam, e não o contrário;
- se faltar um método da porta, a aplicação não sobe (`@abstractmethod`).

A dor que a zona descreve é "muitos dependem de algo concreto que muda". Na v1 era o caso de `llm` (Ca 4) e `config` (Ca 6), e com um adaptador de Ca 1 isso não acontece.

**Limite da leitura:** a métrica conta imports, não o peso deles. Ela não captura, por exemplo, que `main` concentra a composição e as rotas HTTP. A métrica aponta onde olhar; quem diz se a estrutura melhorou são os ADRs e os testes.

## Evidência

```sh
python metrics/coupling.py app/helpdesk main
git worktree add ../v1 v1-coupled && python metrics/coupling.py ../v1/app/helpdesk v1-coupled && git worktree remove ../v1
```

Rodar o script de novo sobre o mesmo código reproduz os CSVs versionados byte a byte. A conferência à mão de 3 componentes da v1 está no README ("Métrica").
