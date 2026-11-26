# Brief entregue ao agente na escala da refatoração

Texto entregue ao agente que fez a escala (ver "Handoff para o agente" em [refactoring-plan.md](refactoring-plan.md)). A única mudança depois do envio foi trocar os dois caminhos da máquina de desenvolvimento por marcadores: o da pasta temporária por `<pasta-temporária>`, e o do repositório por `<repositório>`.

---

Você vai terminar uma refatoração já planejada e pilotada numa aplicação FastAPI de helpdesk. Responda em português.

## Contexto

Repositório: `<repositório>` (Windows; use o Git Bash). O código fica em `app/helpdesk/`. Leia antes de começar:

- `docs/refactoring-plan.md`: o plano, o piloto e as Revisões 1 e 2. Siga a versão revisada.
- O piloto já feito, que é o padrão a seguir: `app/helpdesk/ports.py`, `app/helpdesk/gateway.py`, `app/helpdesk/classification.py` e `app/helpdesk/main.py`.
- O que ainda está como na v1: `suggestion.py`, `extraction.py`, `report.py`, `tickets.py`, `llm.py` e `config.py`.
- `gateway/config.yaml` (as capacidades lógicas) e `compose.yaml`.

## Tarefa

Aplicar às features F2, F3 e F4 a mesma refatoração do piloto e remover o acoplamento restante:

1. `suggestion.py` (F2, capacidade `reply-suggestion`, tarefa `suggest`) e `extraction.py` (F4, capacidade `order-extraction`, tarefa `extract`): cada feature vira uma classe que recebe `LanguageModel` pelo construtor, como `TicketClassifier`, e conhece só o nome lógico da sua capacidade. O prompt continua `f"TASK: <tarefa>\n<conteúdo>"`.
2. `ports.py`: acrescente a porta `TicketSource` (`typing.Protocol`) com `in_period(start: date, end: date) -> list[dict]`.
3. `tickets.py`: vira o adaptador `JsonlTicketSource`, que declara a porta que implementa (Revisão 1) e recebe o caminho do arquivo pelo construtor.
4. `report.py` (F3, capacidade `topics-report`, tarefa `topics`): vira uma classe que recebe `LanguageModel` e `TicketSource` pelo construtor. A lógica de lotes de 150 tickets (constante `TICKETS_PER_CALL` no próprio módulo), a soma por tema, o corte em 3 exemplos e a ordenação continuam exatamente iguais.
5. Remova `llm.py` e `config.py`. O que sobrar de configuração (caminho dos tickets, variável `TICKETS_FILE` com padrão `/data/tickets.jsonl`) é lido em `main.py`, o ponto de composição, que monta os adaptadores e as features.
6. `app/requirements.txt`: remova o SDK `anthropic`, que não é mais usado.
7. `compose.yaml`: o serviço `app` deixa de receber o `.env` (`env_file`) e passa a receber só as variáveis do gateway. Atualize o comentário do piloto.

## Restrições

- O comportamento externo não muda: mesmas rotas, respostas síncronas, mesmos status e corpos, inclusive nos erros (`422` e `502`) e na validação de `start > end` da F3.
- Não altere `tests/`, `provider-fake/`, `edge/`, `data/`, `gateway/`, `metrics/` nem `docs/`.
- Nenhum nome de modelo físico (`gpt-fake-*`, `claude-fake-*`) nem chave de provider em `app/`.
- Nenhuma feature importa um adaptador (`gateway` ou `tickets`); o Ce de cada feature fica contido em {`ports`, `schemas`}. Só `main` importa os adaptadores.
- Nenhuma dependência nova, nenhuma funcionalidade nova, nenhum tratamento de erro novo. Não faça commit.

## Verificação que você deve rodar e relatar

1. `docker compose up -d --build --wait`
2. `.venv/Scripts/python -m pytest tests/characterization -q` (precisa passar inteira).
3. `python metrics/coupling.py app/helpdesk v2-candidate --out "<pasta-temporária>/agent-metrics"` (não grave em `metrics/results`).
4. `grep -rE "gpt-fake|claude-fake" app/` e `grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/` (os dois precisam voltar vazios), e `docker compose exec app env | grep -i fake` (vazio).

No relatório: os arquivos alterados, a saída do pytest, a tabela da métrica e qualquer desvio do plano, com o motivo.
