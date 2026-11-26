# Helpdesk com IA: da integração frágil à arquitetura resiliente

> Fork de [devfullcycle/desafio-arquitetura-era-ia-1](https://github.com/devfullcycle/desafio-arquitetura-era-ia-1). O enunciado original está em [docs/enunciado.md](docs/enunciado.md).

## Visão geral

A aplicação FastAPI do helpdesk não conhece nenhum provider. Cada feature pede uma de quatro capacidades lógicas a um AI Gateway self-hosted (LiteLLM Proxy), por uma porta (`LanguageModel`) que só o adaptador `gateway` implementa. O gateway:

- decide o modelo físico;
- guarda as credenciais dos providers;
- aplica orçamento e limite de requisições por virtual key;
- cuida da resiliência: timeout por tentativa, até 3 tentativas com backoff em falhas transitórias e fallback para o modelo equivalente do outro provider.

Trocar o modelo de uma feature é editar `gateway/config.yaml` e reiniciar só o gateway. As decisões estão no "Mapa de decisões", e a evolução medida, de `v1-coupled` a `v2-decoupled`, em "Métrica".

**Quando um provider cai, nenhuma feature para nem muda de resultado.** O gateway tenta o primário (até 3 vezes em erro `5xx`, uma vez se o provider travar) e passa ao modelo `large` equivalente do outro provider. Se nenhum `large` estiver disponível:

- **classificação, sugestão e relatório** respondem com o modelo `mini`, cuja saída foi medida como suficiente para cada uma;
- **extração de pedido** falha explicitamente com `503`, porque alimenta uma automação sem revisão humana e o `mini` erra 30% das extrações.

Cada feature é entregue no modo que o seu contrato pede ([ADR 0008](docs/adr/0008-modo-de-execucao.md)):

- **F1 (classificação) e F4 (extração):** síncronas, porque respondem em ~1,1 s.
- **F2 (sugestão):** chega por streaming (Server-Sent Events). Com o provider normal, o atendente vê o 1º trecho em menos de 1 s, e não depois de 16 s. Quando o gateway precisa recorrer ao fallback, o 1º trecho atrasa (de ~5 a ~10 s, conforme a falha; ver "Fluxos").
- **F3 (relatório):** assíncrona. O pedido do mês é aceito em milissegundos com `202`, e o relatório completo fica disponível depois, pela URL de status.

Nenhuma requisição termina em `504` da borda, nem com o provider fora ou travado.

## Diagnóstico da v1

Os trechos de `app/` citados nesta seção são os da tag `v1-coupled` (`git show v1-coupled:app/helpdesk/llm.py`). Depois da refatoração, `llm.py` e `config.py` deixaram de existir.

Ambiente: `cp .env.example .env && docker compose up -d --build --wait`, tudo medido pela borda (`localhost:8000`) e conferido em `GET localhost:8090/admin/calls`. Horários em UTC (o formato de `ts` no `/admin/calls`). Horários, IDs de container e custos são os desta execução e mudam a cada vez que o roteiro roda; o que precisa se repetir são os padrões: o `504` em 30 s, o número de tentativas por requisição, as chamadas que continuam depois do `504` e o container recriado. A Dor 1 e o bloco `openai` da Dor 3 rodaram ao mesmo tempo, cada um num provider, e por isso os horários se sobrepõem; o `/admin/calls` separa as chamadas por `provider` e `task`. As demais medições rodaram depois, uma de cada vez. Ticket de referência:

```sh
T='{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
```

Linha de base com o provider em modo normal:

| Feature | Rota | Status | Tempo total (borda) | `/admin/calls` |
|---|---|---|---|---|
| F1 | `POST /tickets/classification` | 200 | 1,13 s | 1 chamada `openai/gpt-fake-large`, `classify`, 1.076 ms |
| F4 | `POST /tickets/extraction` | 200 | 1,09 s | 1 chamada `openai/gpt-fake-large`, `extract`, 1.076 ms |
| F2 | `POST /tickets/reply-suggestion` | 200 | 15,99 s (primeiro byte também em 15,99 s) | 1 chamada `anthropic/claude-fake-large`, `suggest`, `stream: false`, 606 tokens de saída, 15.952 ms |
| F3 (1 dia) | `POST /reports/topics` `2026-08-01..2026-08-01` | 200 | 9,87 s | 1 chamada `topics`, 137 tickets = 6.132 tokens de entrada, 9.804 ms |

Padrões dos SDKs dentro do container `app` (`openai 3.19.0`, `anthropic 1.8.0`), lidos com `docker compose exec app python -c "..."`: `timeout=Timeout(connect=5.0, read=600, write=600, pool=600)` e `max_retries=2` nos dois, ou seja, até **3 tentativas de até 600 s cada**. A borda (`edge/nginx.conf:21`, `proxy_read_timeout 30s`) desiste em 30 s.

### Dor 1: o relatório do mês nunca chega (e continua custando)

```sh
curl -s -w "\n" -X POST localhost:8090/admin/reset
curl -s -w "status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/reports/topics \
  -H "Content-Type: application/json" -d '{"start":"2026-08-01","end":"2026-08-31"}'
# depois, a cada 30 s:
curl -s "localhost:8090/admin/calls?last=5000"
```

Observado:

- A borda responde `504 Gateway Time-out` em **30,02 s** (pedido às 22:23:59, 504 às 22:24:29).
- `/admin/calls` mostra **34 chamadas** `anthropic/claude-fake-large`, `task: topics`, `stream: false`, todas `200`, uma depois da outra (~9,7 s cada), da 22:24:00 até a última às 22:29:23 (terminada às 22:29:32): **~5,5 min** para o relatório inteiro.
- **Só as 3 primeiras chamadas começaram antes do 504.** A 4ª começou às 22:24:30.065, praticamente no instante em que a borda desistiu: o horário do pedido tem resolução de segundo, então não dá para dizer de que lado do 504 ela fica. As 30 seguintes começaram com certeza depois. O `/admin/calls` registra tokens, não dinheiro; o custo abaixo aplica aos tokens registrados o preço do `claude-fake-large` na tabela de Modelos de `provider-fake/README.md` (US$ 3,00 por 1M de entrada e US$ 15,00 por 1M de saída):

  | Chamadas `topics` | Tokens de entrada | Tokens de saída | Custo |
  |---|---|---|---|
  | Todas (34) | 222.114 | 12.162 | 222.114 × 3,00/1M + 12.162 × 15,00/1M = **US$ 0,85** |
  | Da 4ª em diante (31) | 202.139 | 11.079 | 202.139 × 3,00/1M + 11.079 × 15,00/1M = **US$ 0,77 (91%)** |
  | Da 5ª em diante (30, com certeza depois do `504`) | 195.483 | 10.742 | 195.483 × 3,00/1M + 10.742 × 15,00/1M = **US$ 0,75 (88%)** |

  Entre 88% e 91% do custo foram gastos num relatório que nunca é entregue. Cada nova tentativa do painel repete a conta inteira.

Causa:

- `app/helpdesk/main.py:30-31`: rota síncrona; o resultado só é enviado quando o relatório inteiro termina.
- `app/helpdesk/report.py:15-18`: 5.000 tickets em lotes de 150 (`config.py:18`), ou seja, 34 chamadas `topics` em série, cada uma com ~9,7 s (`claude-fake-large`, ~6.500 tokens de entrada em média). São ~5,5 min sem enviar nenhum byte, contra os 30 s da borda.
- Desistir não é cancelar: a rota é `def` (roda no threadpool do Starlette) e nada observa a desconexão do cliente. Depois do `504`, o laço de `report.py:15` segue chamando o provider até o fim, e alguém paga esses tokens por uma resposta que ninguém vai receber.

### Dor 2: o atendente olha uma tela parada esperando a sugestão

```sh
curl -sN -o /dev/null -w "status=%{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s\n" \
  -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"

# o mesmo com o modelo lento
curl -s -w "\n" -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "model": "claude-fake-large", "mode": "slow"}'
curl -sN -o /dev/null -w "status=%{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s\n" \
  -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"
curl -s "localhost:8090/admin/calls?last=1"   # repetir até duration_ms aparecer
curl -s -w "\n" -X POST localhost:8090/admin/reset
```

Observado:

- Modo normal: `status=200 ttfb=15.99s total=15.99s`. O primeiro byte chega junto com o último: durante 16 s o atendente não vê nada. Em `/admin/calls`, a chamada `suggest` tem `stream: false`, 606 tokens de saída em 15.952 ms (800 ms até o primeiro token + 606 / 40 tokens/s).
- `claude-fake-large` em `slow`: `status=504 ttfb=30.02s total=30.02s`. A borda desiste sem ter recebido nenhum byte. A chamada `suggest` começou às 22:48:16 e só terminou no provider 159.501 ms depois, com `200` e os mesmos 606 tokens de saída: um texto gerado e pago **2 min 9 s depois** de o atendente ter recebido o erro.

Causa:

- `app/helpdesk/llm.py:21-25`: `messages.create` sem `stream=True`; o SDK só devolve quando a geração inteira termina.
- `app/helpdesk/suggestion.py:6-7` e `app/helpdesk/main.py:20-21`: a rota devolve um JSON único com o texto completo. O modelo já gera em ~0,8 s o primeiro trecho, mas a arquitetura o retém por mais 15 s. Com um provider mais lento, essa retenção passa dos 30 s da borda.

### Dor 3: com um provider fora do ar ou travado, parte do helpdesk para

```sh
curl -s -w "\n" -X POST localhost:8090/admin/reset
curl -s -w "\n" -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "openai", "mode": "error_500"}'
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/extraction -H "Content-Type: application/json" -d "$T"
curl -s -o /dev/null -w "status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"
curl -s -o /dev/null -w "status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/reports/topics -H "Content-Type: application/json" -d '{"start":"2026-08-01","end":"2026-08-01"}'
curl -s "localhost:8090/admin/calls?last=10"

curl -s -w "\n" -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "openai", "mode": "timeout"}'
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
curl -s "localhost:8090/admin/calls?last=10"   # repetir: a 2ª tentativa aparece ~300 s depois da 1ª
# depois que a 2ª tentativa aparecer, voltar o openai ao normal para ver a 3ª concluir sem ninguém esperando
curl -s -w "\n" -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "openai", "mode": "normal"}'
curl -s "localhost:8090/admin/calls?last=10"   # ~300 s depois: 3ª tentativa com status 200

curl -s -w "\n" -X POST localhost:8090/admin/reset
curl -s -w "\n" -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "mode": "error_500"}'
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/reports/topics -H "Content-Type: application/json" -d '{"start":"2026-08-01","end":"2026-08-01"}'
curl -s -w " status=%{http_code} total=%{time_total}s\n" -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
curl -s -w "\n" -X POST localhost:8090/admin/reset
```

Observado:

- `openai` em `error_500`: F1 e F4 respondem `500 Internal Server Error` (texto puro, sem corpo JSON) em ~1,4 s. `/admin/calls` mostra **3 chamadas** `gpt-fake-large` com status `500` para cada requisição (1 + 2 retries do SDK), todas no mesmo provider. Nenhuma tentativa no `anthropic`, onde o modelo equivalente estava saudável. No log do `app`, `openai.InternalServerError` sem tratamento (traceback do ASGI). No mesmo cenário, F2 responde `200` em 15,97 s e F3 (1 dia) `200` em 9,90 s, ambas pelo `anthropic`.
- `openai` em `timeout`: F1 recebe `504 Gateway Time-out` da borda em **30,01 s** (22:24:43). A aplicação não desiste. `/admin/calls` mostra:

  | Tentativa | Início | Status no provider | `duration_ms` |
  |---|---|---|---|
  | 1ª | 22:24:13 | `504` (modo `timeout`) | 300.000 |
  | 2ª | 22:29:13 | `504` (modo `timeout`) | 300.001 |
  | 3ª | 22:34:14 | `200` (o `openai` voltou a `normal` às 22:29:29, como no roteiro) | 1.076 |

  A aplicação concluiu e pagou a classificação **10 minutos depois** de o cliente ter recebido o 504. Entre uma tentativa e outra, o SDK esperou de 0,5 a 0,8 s de backoff.

  Estimativa, não medida: sem a volta ao `normal`, a 3ª tentativa também esperaria 300 s e terminaria em `504`. Pelo `max_retries=2` do SDK e os 300 s medidos por tentativa, seriam **~15 min** até o SDK desistir e o app responder `500`. Durante todo esse tempo, a requisição ocupa uma das 40 threads do threadpool padrão (anyio), onde o Starlette roda as rotas `def`.
- `anthropic` em `error_500`: F2 (`/tickets/reply-suggestion`) e F3 (`/reports/topics`) respondem `500` em ~1,4 s, cada uma com 3 chamadas `claude-fake-large` com status `500` em `/admin/calls`; no mesmo instante a F1 responde `200` pelo `openai`. Em qualquer dos dois casos, **metade do helpdesk para junto com um provider**, embora o modelo equivalente do outro provider esteja saudável.

Causa:

- `app/helpdesk/config.py:10-13`: cada feature está presa a um único modelo físico de um único provider; não existe destino alternativo.
- `app/helpdesk/llm.py:8-9`: clientes criados sem `timeout` nem `max_retries`, valendo os padrões (600 s de leitura, 2 retries). Uma chamada travada segura a requisição por até 3 × 300 s no modo `timeout`, muito além dos 30 s da borda; um `500` vira 3 chamadas ao mesmo provider caído (cada uma registrada em `/admin/calls` com `input_tokens: 19` e `output_tokens: null`).
- `app/helpdesk/classification.py:11` e `extraction.py:12`: a exceção do SDK não é tratada; o cliente recebe `500` genérico, sem dizer o que aconteceu.

### Dor 4: trocar o modelo de uma feature exige mudar código e reconstruir

Alteração temporária em `app/` só para a demonstração, desfeita em seguida (`git checkout -- app/helpdesk/config.py`, `git status` limpo em `app/`), antes da tag `v1-coupled`:

```sh
# 1. a única forma de trocar o modelo da F1 é editar o código
sed -i 's/^CLASSIFICATION_MODEL = "gpt-fake-large"/CLASSIFICATION_MODEL = "gpt-fake-mini"/' app/helpdesk/config.py
# 2. reiniciar não basta
docker compose restart app
curl -s -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
curl -s "localhost:8090/admin/calls?last=1"
# 3. é preciso reconstruir a imagem
docker compose up -d --build --wait app
curl -s -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
curl -s "localhost:8090/admin/calls?last=1"
# 4. desfazer
git checkout -- app/helpdesk/config.py && docker compose up -d --build --wait app
```

Observado:

| Passo | Container `app` | `/admin/calls` da F1 |
|---|---|---|
| Inicial | `76164335e7cc` | `openai/gpt-fake-large` |
| `config.py` editado + `docker compose restart app` | `76164335e7cc` (o mesmo) | `openai/gpt-fake-large`: **a troca não aconteceu** |
| `docker compose up -d --build app` | `a0f3fdcccd35` (novo) | `openai/gpt-fake-mini`, F1 em 0,43 s |
| Alteração desfeita + rebuild | `cdc5098c2271` (novo) | `openai/gpt-fake-large` |

O passo 3 (`docker compose up -d --build --wait app` com o `config.py` alterado) levou **111 s** do comando até o container novo ficar saudável. Trocar um modelo virou um deploy da aplicação, com mudança de código, imagem nova e container recriado.

Causa:

- `app/helpdesk/config.py:10-13`: o nome do modelo físico é uma constante no código-fonte, não uma configuração. E não só o nome: `config.py:3-7` também fixa as chaves e as base URLs de cada provider, e `llm.py:13-26` fixa o formato de API de cada um. Trocar para um modelo de outro provider exige ainda mudar a função chamada em cada feature (`classification.py:11`, `extraction.py:12`, `suggestion.py:6`, `report.py:18`).
- `app/Dockerfile:6`: `COPY helpdesk ./helpdesk` congela o código na imagem. As chaves já chegam pelo ambiente (`env_file: .env` no `compose.yaml`, lidas em `config.py:3-4`), mas o nome do modelo não: não há volume nem variável de ambiente que permita trocá-lo sem reconstruir.

A quarta fricção proposital, "o modelo barato não é tão bom", foi verificada no reconhecimento e depois medida nos 5.000 tickets. A evidência está em "Tabela de fallback" e no [ADR 0004](docs/adr/0004-politica-de-fallback.md).

## Como rodar

```sh
cp .env.example .env
docker compose up -d --build --wait
curl -s localhost:8000/health
```

A primeira subida leva ~3 min, quase tudo no gateway: ele cria o schema do banco e aplica as virtual keys antes de ficar saudável. Os serviços e as portas no host:

| Serviço | Porta no host | Papel |
|---|---|---|
| `edge` | 8000 | a borda (nginx), porta de entrada oficial do helpdesk |
| `app` | não publicada | a aplicação FastAPI; só fala com o `gateway` |
| `gateway` | 4000 | o AI Gateway (LiteLLM Proxy); configuração em `gateway/` |
| `gateway-db` | não publicada | o Postgres do gateway: virtual keys e gasto (ADR 0007) |
| `provider-fake` | 8090 | o provider simulado e os endpoints `/admin` |

### A primeira chamada depois da subida

A 1ª chamada real de um processo do gateway paga uma inicialização única do LiteLLM, qualquer que seja a capacidade. Por isso, o app e o gateway se aquecem na subida, antes de ficarem saudáveis:

- **o `app`** abre a conexão com o gateway e lista os modelos com a sua chave (`GET /models`), o que também passa pela autenticação do gateway;
- **o `gateway`** (`gateway/bootstrap_keys.py`) sobe por alguns segundos um servidor local, dentro do próprio container, que responde no formato da OpenAI. Em seguida, chama o destino interno `gateway-warmup`, que aponta para esse servidor. A chamada percorre o caminho completo do gateway sem chegar ao provider e sem nada em `/admin/calls`.

Medido pela borda, com várias amostras por situação:

| Situação | 1ª F1, sem o aquecimento do gateway | 1ª F1, com o aquecimento | 1º trecho da 1ª F2, com o aquecimento |
|---|---|---|---|
| `docker compose down` + `up -d --wait` | 1,55 a 1,60 s (2,74 s logo depois de ligar a máquina) | 1,14 s | 0,93 s |
| Só o `gateway` reiniciado | 1,64 a **4,76 s** (5 amostras; 1,7 a 2,8 s na revisão independente) | 1,12 a 1,17 s (3 amostras) | — |
| Só o `app` reiniciado | 1,12 a 1,67 s | igual | 0,85 a 0,93 s |
| Chamadas seguintes | F1 ~1,1 s; F2 ~0,9 s | igual | igual |

Sem o aquecimento do gateway, a 1ª F1 passou do limite de 3 s numa das amostras (4,76 s). As alternativas avaliadas e o motivo de cada escolha estão no [ADR 0008](docs/adr/0008-modo-de-execucao.md).

### Testes de caracterização

A suíte em `tests/characterization/` roda do host contra o compose no ar, sempre pela borda (`localhost:8000`). Ela começa com `POST localhost:8090/admin/reset` para garantir o provider em modo normal, o que também limpa o registro do `/admin/calls`. Pré-requisito: Python 3.12, de preferência num venv.

```sh
python -m pip install -r tests/characterization/requirements.txt   # uma vez
python -m pytest tests/characterization
```

As URLs podem ser trocadas pelas variáveis `HELPDESK_URL` e `PROVIDER_URL`. A suíte leva ~1,5 min, porque F2 e F3 levam de 10 a 20 s por chamada.

Para cada caso, a suíte fixa o status, o `Content-Type` e o corpo inteiro da resposta (campos e valores), conforme o comportamento da `v1-coupled`. A suíte é a mesma nas tags `v1-coupled` e `v2-decoupled`. Na `main`, os testes da F2 e da F3 foram atualizados para o contrato novo (streaming e assíncrono, [ADR 0008](docs/adr/0008-modo-de-execucao.md)). Eles conferem que o conteúdo continua o mesmo gravado da v1: o texto concatenado da F2 e o relatório da F3 batem com `golden/`.

| Feature | Casos de sucesso | Erros de entrada (`422`) |
|---|---|---|
| F1 | 8 tickets, cobrindo as 5 categorias e as 3 prioridades | `text` vazio, sem `ticket_id`, JSON malformado |
| F2 | 2 sugestões completas, gravadas em `golden/` | sem `text`, `text` numérico |
| F3 | 1 dia (1 lote), 2 dias (274 tickets em 2 lotes, o que fixa a soma dos lotes) e um período sem tickets, todos gravados em `golden/` exceto o vazio | `start` depois de `end`, data inexistente, sem `end` |
| F4 | 8 tickets: nota fiscal antes do pedido, pedido sem `#`, com e sem produto | `ticket_id` vazio |

Nos casos de erro e no período sem tickets, a suíte também confere em `/admin/calls` que nenhuma chamada chegou ao provider.

### Testes dos fluxos

A suíte em `tests/flows/` confere pela borda os critérios de "Fluxos de chamada" e de fallback do enunciado. São 9 testes:
- os prazos (F1 em até 3 s, 1º trecho da F2 em até 1,5 s e antes da metade do tempo total, `202` da F3 em até 1 s);
- o relatório do mês com `total_tickets` 5000;
- os cenários de falha do provider, conferidos em `/admin/calls`: fallback técnico, F1 com o primário travado, evento `error` no meio do stream, F3 em `failed` em até 60 s, e a tabela de fallback sem nenhum `large`.

Ela usa os mesmos requisitos da suíte de caracterização e limpa o `/admin/calls` a cada teste.

```sh
python -m pytest tests/flows                  # ~9 min, com o relatório do mês inteiro
python -m pytest tests/flows -m "not slow"    # ~2 min, sem ele
```

A suíte de caracterização detecta a troca de um `large` por um `mini` em F2 e F4. Com o `mini`, a sugestão da F2 muda, e 5 dos 7 tickets do dataset da F4 dão outro resultado: por exemplo, o `TK-00002` devolve a nota fiscal `#530720` no lugar do pedido. Em F1 e F3, o `mini` dá a mesma saída (ver "Evidência: modelos `large` × `mini`").

### Métrica de acoplamento

O script `metrics/coupling.py` usa só a biblioteca padrão (Python 3.12). Ele recebe a pasta do pacote a medir e um nome, e grava `metrics/results/<nome>.csv` e `metrics/results/<nome>.svg`:

```sh
# o código do checkout atual
python metrics/coupling.py app/helpdesk main

# uma tag: o script da branch atual sobre um checkout dela
git worktree add ../v1 v1-coupled
python metrics/coupling.py ../v1/app/helpdesk v1-coupled
git worktree remove ../v1
```

## Métrica

A régua é a do requisito 3: componente é cada módulo `.py` de `app/helpdesk/` (inclusive em subpastas, exceto `__init__.py`), dependência é um import que resolve para outro componente, e I, A e D seguem as fórmulas do enunciado. Onde o enunciado deixa espaço, o script decide assim (está no docstring de `metrics/coupling.py`):

- conta qualquer import do módulo, inclusive dentro de funções;
- um import que resolve para um pacote (o `__init__.py`) não conta, porque o pacote não é componente;
- uma classe é abstrata quando `typing.Protocol` ou `abc.ABC` está entre as suas bases diretas;
- os valores são calculados com frações exatas e arredondados para 2 casas, com meio para cima.

O script foi conferido contra um pacote sintético com subpastas, imports relativos de 1 e 2 níveis, import absoluto, import dentro de função e as variações de `Protocol` e `ABC`. Rodar duas vezes sobre o mesmo checkout gera CSV e SVG idênticos byte a byte.

### v1-coupled

![Gráfico A × I da v1-coupled](metrics/results/v1-coupled.svg)

Resultado em [metrics/results/v1-coupled.csv](metrics/results/v1-coupled.csv). Conferência à mão de três módulos, como o enunciado sugere:

| Componente | Quem importa (Ca) | O que importa (Ce) | I | A | D |
|---|---|---|---|---|---|
| `config` | `classification`, `extraction`, `llm`, `report`, `suggestion`, `tickets` (6) | nada além de `os` (0) | 0 / (0 + 6) = 0,00 | sem classes: 0,00 | \|0 + 0 − 1\| = 1,00 |
| `llm` | `classification`, `extraction`, `report`, `suggestion` (4) | `config` (1); `openai`, `anthropic` e `json` são externos | 1 / (1 + 4) = 0,20 | sem classes: 0,00 | \|0 + 0,2 − 1\| = 0,80 |
| `report` | `main` (1) | `config`, `llm`, `tickets`, `schemas` (4) | 4 / (4 + 1) = 0,80 | sem classes: 0,00 | \|0 + 0,8 − 1\| = 0,20 |

Leitura:

- **Nenhum componente tem abstração.** A = 0 em todos; as 7 classes de `schemas` são modelos pydantic concretos. Todos os pontos ficam no eixo horizontal, e a distância da Main Sequence é só 1 − I.
- **Três componentes na zona de dor:** `config` (D 1,00), `schemas` (D 1,00) e `llm` (D 0,80). São os mais importados (Ca 6, 5 e 4) e totalmente concretos, então qualquer mudança neles se propaga para as features.
  - `llm` é onde mora o acoplamento com os providers: dois SDKs, dois formatos de API, clientes sem timeout (Dores 2 e 3). É concreto, muito dependido e volátil de verdade. Pela régua, o componente que chama o modelo não pode ser exceção.
  - `config` junta chaves, base URLs e nomes de modelos físicos, os detalhes mais voláteis do sistema (Dor 4), e é o componente mais importado. Também é dor real.
  - `schemas` são os tipos de valor do contrato HTTP das 4 features, que só mudam quando o contrato muda. É candidato a "estável por natureza" no ADR da métrica.
- **As features** (`classification`, `extraction`, `suggestion` e `report`) têm I de 0,75 a 0,80 e D de 0,20 a 0,25. Ficam perto da Main Sequence, mas o problema delas aparece do outro lado da dependência: todas importam diretamente o `llm` e o `config` concretos.
- **`main`** (I 1,00, D 0,00) é o ponto de composição. **`tickets`** (I 0,50, D 0,50) fica na fronteira da zona de dor, sem entrar nela.

### v2-decoupled

![Gráfico A × I da v2-decoupled](metrics/results/v2-decoupled.svg)

Resultado em [metrics/results/v2-decoupled.csv](metrics/results/v2-decoupled.csv). O caminho até aqui (plano, piloto medido antes e depois, revisões e handoff para o agente) está em [docs/refactoring-plan.md](docs/refactoring-plan.md).

Leitura, comparando com a v1:

- **`llm` e `config` saíram do gráfico.** Os dois estavam na zona de dor (D 0,80 e 1,00) e concentravam SDK, credencial e nome de modelo. O nome do modelo e as chaves foram para o gateway. O que sobrou de configuração (URL e chave do gateway, caminho dos tickets) é lido em `main`, o ponto de composição.
- **`ports` é o novo centro.** É o componente mais dependido (Ca 6: as 4 features e os 2 adaptadores) e é totalmente abstrato (2 `Protocol`s). Fica no canto estável-abstrato, sobre a Main Sequence (D 0,00): o que muitos usam é justamente o que não tem implementação para mudar.
- **As features dependem só da porta e do contrato.** `classification`, `extraction`, `suggestion` e `report` têm Ce {`ports`, `schemas`}, I 0,67 e D 0,33. Na v1 tinham I de 0,75 a 0,80, mas dependiam de `llm` e `config` concretos.
- **Os adaptadores são detalhes que dependem da abstração.** `gateway` (o único com SDK) e `tickets` declaram a porta que implementam, e só `main` os importa. Ficam com I 0,50 e D 0,50: fora da zona de dor, mas exatamente na fronteira, já que a zona exige I < 0,5. Isso não estava no plano original. O piloto mostrou o adaptador na zona de dor quando ele implementava a porta só estruturalmente, e a mudança foi motivada pela métrica, como o plano registra (Revisão 1). O que ela entrega à estrutura: a seta adaptador → porta escrita no código, e portas com métodos abstratos, de modo que um adaptador incompleto não sobe.
- **`schemas` continua na zona de dor** (D 1,00). São os tipos de valor do contrato HTTP, que só mudam quando o contrato muda. Está declarado "estável por natureza" no [ADR 0009](docs/adr/0009-leitura-da-metrica.md).

### main

![Gráfico A × I da main](metrics/results/main.svg)

Resultado em [metrics/results/main.csv](metrics/results/main.csv). Leitura, comparando com a v2:

- **Na zona de dor, só `schemas`**, a exceção declarada no [ADR 0009](docs/adr/0009-leitura-da-metrica.md): tipos de valor do contrato HTTP, agora com o `TaskStatus` da F3. Nenhum outro componente está na zona. O componente que chama o gateway (`gateway`) e as quatro features estão fora dela.
- **`ports` cresceu com os modos de execução.** Ganhou o `stream` da F2, a porta `TaskRunner` da F3 e as exceções do contrato (`ModelUnavailable` e `ModelRejectedInput`). Com 3 `Protocol`s e 2 exceções, A caiu para 0,60. Como passou a importar `schemas` (o `TaskStatus`), I subiu para 0,11. O D de 0,29 continua perto da Main Sequence, e `ports` segue o componente mais dependido (Ca 8).
- **Nasceu o adaptador `background`** (I 0,67, D 0,33), o pool de threads da F3, que implementa `TaskRunner` e é importado só por `main`. É o mesmo desenho de `gateway` e `tickets`: as features não sabem como a tarefa roda.
- **As features continuam com Ce {`ports`, `schemas`}.** A F3 assíncrona e a F2 em streaming não trouxeram nenhuma dependência concreta para elas. O SDK continua importado só por `gateway`, e os adaptadores, só por `main` (o ponto de composição, I 1,00).
- **`gateway` e `tickets` continuam na fronteira** (I 0,50, D 0,50), como na v2.

## Tabela de capacidades

A aplicação conhece só as 4 capacidades lógicas, uma por feature ([ADR 0003](docs/adr/0003-capacidades-logicas.md)). Os destinos, timeouts e tentativas ficam em [gateway/config.yaml](gateway/config.yaml) ([ADR 0005](docs/adr/0005-timeout-e-retry.md)).

| Capacidade lógica | Feature | Modelo primário | Fallback técnico | Política com modelo fraco | Timeout por tentativa |
|---|---|---|---|---|---|
| `ticket-classification` | F1 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | aceita: `openai/gpt-fake-mini` | 5 s |
| `reply-suggestion` | F2 | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | aceita: `anthropic/claude-fake-mini` | 5 s até o 1º trecho (streaming) |
| `topics-report` | F3 | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | aceita: `anthropic/claude-fake-mini` | 15 s por lote |
| `order-extraction` | F4 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | recusa: falha explícita | 5 s |

**Número máximo de tentativas por destino** (primário, fallback técnico e, se houver, modelo fraco), o mesmo em todas as capacidades:

| Falha no destino | Tentativas no destino | Depois disso |
|---|---|---|
| `5xx` ou `429` (transitória) | **3** (1 + 2 retries, com backoff exponencial) | próximo destino |
| timeout (o provider não envia bytes dentro do prazo da capacidade) | **1** (sem retry) | próximo destino |
| outros `4xx` (ex.: entrada maior que o contexto do modelo) | **1** (sem retry) | próximo destino: o LiteLLM aplica a lista de fallbacks a qualquer erro. Se todos recusarem, a feature responde `422` |

O SDK da aplicação não repete nada (`max_retries=0`): as tentativas acontecem só no gateway. Cada feature tem ainda um prazo no app para esperar o gateway (`TIMEOUT_SECONDS`): 14 s na F1 e na F4; 20 s sem receber nada na F2; 50 s por lote na F3. Estourado esse prazo, ou esgotados os destinos:
- a F1 e a F4 respondem `503` explícito;
- a F2 responde `503`, se nada tiver sido enviado, ou o evento `error`, se o stream já tiver começado;
- a tarefa da F3 vai para `failed`, com o motivo. Se os destinos recusarem a entrada (`400` do gateway), ela responde `422`, porque tentar de novo não adianta.

## Tabela de fallback

Com **um provider fora do ar**, nenhuma feature muda de resultado. A capacidade que usa esse provider como primário cai no fallback técnico, o `large` equivalente do outro provider, e responde `200` com a mesma saída.

Sem **nenhum modelo `large` disponível**, cada feature faz o que o [ADR 0004](docs/adr/0004-politica-de-fallback.md) decidiu:

| Feature | Sem nenhum `large` | Status | Por quê (evidência nos 5.000 tickets) |
|---|---|---|---|
| F1 Classificar | responde com a classificação do `gpt-fake-mini` | `200` | o `mini` deu a mesma saída em 5.000 de 5.000 tickets |
| F2 Sugerir resposta | faz o streaming da sugestão do `claude-fake-mini`, mais curta, até o `event: end` | `200` | numa amostra de 200, os mesmos passos de orientação, a mesma abertura e o mesmo fechamento, em 2/5 do tamanho; o atendente revisa antes de enviar |
| F3 Relatório | produz o relatório com o `claude-fake-mini`: a tarefa chega a `303` → resultado | `202`, depois `303` e `200` | o mesmo relatório: 0 diferenças nos 34 lotes do mês |
| F4 Extrair pedido | falha explícita, sem chamar o `mini`: `{"detail": "Nenhum modelo disponível...", "capability": "order-extraction", "reason": "..."}` | `503` | o `mini` erra 30,7% (pedido trocado pela nota fiscal em 14,3%, produto perdido em todos), e a saída vai para uma automação sem revisão humana |

### Evidência: modelos `large` × `mini`

A comparação completa foi feita por [scripts/compare_models.py](scripts/compare_models.py), que chama o provider direto com a mesma entrada nos dois tamanhos (~11 min com todas as tarefas, com o compose no ar). Um terceiro argumento opcional escolhe as tarefas (ex.: `extract,suggest`):

```sh
python scripts/compare_models.py data/tickets.jsonl docs/adr/evidence/large-vs-mini.json
```

| Tarefa | Entrada | `large` × `mini` |
|---|---|---|
| `classify` | 5.000 tickets | 0 diferenças |
| `topics` | 34 lotes de 150 (o mês inteiro) | 0 diferenças |
| `suggest` | amostra de 200 (1 a cada 25) | texto médio de 2.431 para 1.066 caracteres; nos 200, o mesmo conjunto de passos distintos, a mesma abertura e o mesmo fechamento |
| `extract` | 5.000 tickets | 1.533 diferenças: 717 números de pedido errados, todos com o número da nota fiscal do ticket; 953 de 953 produtos perdidos |

O resultado está em [docs/adr/evidence/large-vs-mini.json](docs/adr/evidence/large-vs-mini.json). Para ver o caso mais grave em segundos (`extract` no `TK-00002`), troque só o `model`:

```sh
for m in gpt-fake-large gpt-fake-mini; do
  curl -s localhost:8090/openai/v1/chat/completions \
    -H "Authorization: Bearer sk-fake-openai-0001" -H "Content-Type: application/json" \
    -d "{\"model\": \"$m\", \"messages\": [{\"role\": \"user\", \"content\": \"TASK: extract\nTenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065.\"}]}" \
    | python -c "import json,sys; print('$m', json.load(sys.stdin)['choices'][0]['message']['content'])"
done
```

## Fluxos

O modo de cada feature e as perguntas que o decidiram estão no [ADR 0008](docs/adr/0008-modo-de-execucao.md). Todos os comandos são pela borda (`localhost:8000`).

```sh
T='{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
```

### F1 Classificar ticket: síncrono

O resultado completo vem no corpo, em ~1,1 s, inclusive na 1ª chamada depois da subida (ver "A primeira chamada depois da subida"). Sem modelo disponível, a resposta é `503` com `{"detail", "capability", "reason"}`.

```sh
curl -s -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T"
# {"ticket_id":"TK-00042","category":"delivery","priority":"high"}
```

### F2 Sugerir resposta: streaming (Server-Sent Events)

`200` com `Content-Type: text/event-stream`. Cada trecho chega num evento `data: {"chunk": ...}`, e o fim, no evento `end`. O 1º trecho chega em ~0,9 s, inclusive na 1ª chamada depois da subida (ver "A primeira chamada depois da subida"), e o texto completo em ~17 s. Para montar a sugestão, concatene os `chunk` na ordem.

```sh
curl -N -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"
# data: {"chunk": "Olá!"}
# data: {"chunk": " Sin"}
# ...
# event: end
# data: {"ticket_id": "TK-00042"}
```

Se a geração falhar depois do 1º trecho, quando o `200` já foi enviado, o stream termina com o evento `error`. Para ver isso, coloque o provider da F2 em `midstream_error`:

```sh
curl -s -X POST localhost:8090/admin/failures -H "Content-Type: application/json" -d '{"provider": "anthropic", "mode": "midstream_error"}'
curl -N -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" -d "$T"
# data: {"chunk": ...}  (cerca de 1/4 do texto)
# event: error
# data: {"ticket_id": "TK-00042", "message": "A geração foi interrompida"}
curl -s -X POST localhost:8090/admin/reset
```

Uma falha antes do 1º trecho (nenhum destino disponível) ainda vira status HTTP: `503`, ou `422` se a entrada for recusada.

Quando o gateway precisa de outro destino, ele troca antes do 1º trecho, e o stream chega completo, só mais tarde. Medido pela borda:

| Situação do provider | 1º trecho | Quem responde |
|---|---|---|
| normal | ~0,9 s | `claude-fake-large` |
| `anthropic` em `error_500` | ~5,2 s (3 tentativas com backoff) | `gpt-fake-large` |
| `anthropic` em `timeout` | ~6,0 s (1 tentativa cortada em 5 s) | `gpt-fake-large` |
| os dois `large` em `error_500` | ~10,4 s (3 + 3 tentativas) | `claude-fake-mini` |
| `anthropic` em `midstream_error` | ~0,9 s, e o stream termina em `event: error` | não há fallback depois que o texto começou: o gateway não troca de destino no meio do stream |

### F3 Relatório de temas: assíncrono (Asynchronous Request-Reply)

O pedido é aceito na hora com `202`, `Location` e `Retry-After`. Consulte a URL de status até ela responder `303 See Other`, que aponta para o relatório. O mês inteiro leva ~6 min.

```sh
curl -s -i -X POST localhost:8000/reports/topics -H "Content-Type: application/json" \
  -d '{"start": "2026-08-01", "end": "2026-08-31"}'
# HTTP/1.1 202 Accepted
# location: /reports/topics/status/<id>
# retry-after: 5
# {"task_id":"<id>","state":"pending"}

curl -s -i localhost:8000/reports/topics/status/<id>
# 200 {"state":"running","progress":"1800/5000"}      (enquanto processa)
# 200 {"state":"failed","reason":"..."}                (se não puder terminar; em até 60 s)
# 303 See Other, location: /reports/topics/<id>       (quando termina)

curl -s localhost:8000/reports/topics/<id>
# {"start":"2026-08-01","end":"2026-08-31","total_tickets":5000,"topics":[...]}
```

Para acompanhar até o fim num comando só, o `curl -L` segue o `303` até o relatório. O `%header{}` exige curl 7.84 ou mais novo:

```sh
LOC=$(curl -s -o /dev/null -w "%header{location}" -X POST localhost:8000/reports/topics \
  -H "Content-Type: application/json" -d '{"start": "2026-08-01", "end": "2026-08-31"}')
until curl -s -o /dev/null -w "%{http_code}" "localhost:8000$LOC" | grep -qE "303"; do
  curl -s "localhost:8000$LOC"; echo; sleep 5
done
curl -s -L "localhost:8000$LOC" | head -c 300
```

Erros de entrada continuam síncronos: `422` para `start` depois de `end`, data inválida ou campo faltando. Um id desconhecido dá `404`, e pedir o resultado antes do fim dá `409`.

Limites da F3:
- **Memória:** as tarefas ficam na memória do `app` e somem se ele reiniciar; as 100 últimas terminadas continuam consultáveis.
- **Fila:** até 4 relatórios rodam ao mesmo tempo, e um 5º espera `pending`. O prazo de 60 s para chegar a `failed` conta a partir do início da execução: medido com os dois providers em `error_500`, de 14 a 17 s; em `timeout`, ~46 s.

### Campos além dos "Contratos sugeridos"

- O `202` da F3 traz o corpo `{"task_id", "state": "pending"}`, além dos headers do contrato.
- O status da F3 em `running` pode trazer `progress` (tickets processados / total), como no exemplo do enunciado.
- Quando nenhum destino responde, as respostas `503` (F1, F2 antes do 1º trecho e F4) trazem `{"detail", "capability", "reason"}`. O `422` de entrada recusada pelo modelo traz o mesmo formato. `capability` é o nome lógico, e `reason` diz o que o gateway respondeu.
- O `reason` de uma tarefa da F3 em `failed` tem a forma `"<capacidade>: <motivo>"`, por exemplo `"topics-report: gateway respondeu HTTP 500"`.

### F4 Extrair dados do pedido: síncrono

O resultado completo vem no corpo, em ~1,1 s. Sem modelo `large`, a resposta é `503` explícito (ADR 0004).

```sh
curl -s -X POST localhost:8000/tickets/extraction -H "Content-Type: application/json" -d "$T"
# {"ticket_id":"TK-00042","order_number":"#481516","product":null}
```

## Troca de modelo

**Arquivo:** [gateway/config.yaml](gateway/config.yaml). Cada destino aponta para um modelo físico por uma âncora YAML (`*openai-large`, `*openai-mini`, `*anthropic-large`, `*anthropic-mini`); endereço, credencial e preço de cada modelo ficam declarados uma vez no topo do arquivo.

**O que alterar:** a âncora do destino. Por exemplo, para a F1 passar a usar o modelo mais barato da OpenAI, que deu a mesma classificação que o `large` nos 5.000 tickets e custa ~1/17 do preço por token (US$ 0,15 contra 2,50 por 1M de entrada; 0,60 contra 10 por 1M de saída) ([ADR 0004](docs/adr/0004-politica-de-fallback.md)):

```yaml
  - model_name: ticket-classification
    litellm_params: { <<: [*openai-mini, *short-timeout] }   # era *openai-large
```

Ao trocar o **provider** do primário, troque também o do fallback técnico (`ticket-classification-fallback`), para que os dois continuem em providers diferentes, como exige o [ADR 0002](docs/adr/0002-providers-e-teto-de-gasto.md).

**Comando:** reiniciar só o gateway. O `app` não é reconstruído nem reiniciado.

```sh
docker compose restart gateway && docker compose up -d --wait gateway
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" \
  -d '{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
curl -s "localhost:8090/admin/calls?last=1"    # a chamada já aparece no novo modelo
docker compose ps app                          # o app continua de pé desde antes da troca
```

Testado com o exemplo acima: a chamada seguinte apareceu em `/admin/calls` como `openai/gpt-fake-mini`, e o container do `app` manteve o mesmo id e o mesmo horário de início. A troca para `*anthropic-large` também foi testada, com o mesmo resultado (`anthropic/claude-fake-large`).

## Roteiro de governança

As virtual keys são declaradas em [gateway/keys.yaml](gateway/keys.yaml) e criadas na subida do gateway, sem passo manual ([ADR 0006](docs/adr/0006-governanca-de-chaves.md)). Além da chave do app (US$ 100 a cada 30 dias e 600 requisições por minuto), há duas chaves de demonstração com limites pequenos, para a recusa aparecer na hora.

**Recusa por orçamento.** A chave `demo-budget` tem teto de US$ 0,0001, e uma classificação custa ~US$ 0,00014: a 1ª requisição passa e estoura o teto, e as seguintes são recusadas. O roteiro começa zerando o gasto acumulado da chave, com a master key, para ser repetível.

```sh
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:4000/key/sk-demo-budget-0001/reset_spend \
  -H "Authorization: Bearer sk-gateway-master-0001" -H "Content-Type: application/json" -d '{"reset_to": 0}'
for i in 1 2 3; do
  curl -s -w "  -> HTTP %{http_code}\n" localhost:4000/chat/completions \
    -H "Authorization: Bearer sk-demo-budget-0001" -H "Content-Type: application/json" \
    -d '{"model": "ticket-classification", "messages": [{"role": "user", "content": "TASK: classify\nMeu pedido não chegou"}]}'
  sleep 2
done
curl -s "localhost:8090/admin/calls?last=10"   # 1 chamada: as recusadas não chegaram ao provider
```

Resultado: 1ª `200`; 2ª e 3ª `429` com `"type": "budget_exceeded"` ("Budget has been exceeded!"). Sem o `reset_spend`, uma chave já usada recusa também a 1ª, porque o gasto acumulado fica no banco do gateway.

**Recusa por limite de requisições.** A chave `demo-rate-limit` aceita 2 requisições por minuto.

```sh
curl -s -X POST localhost:8090/admin/reset
for i in 1 2 3 4; do
  curl -s -w "  -> HTTP %{http_code}\n" localhost:4000/chat/completions \
    -H "Authorization: Bearer sk-demo-rate-limit-0001" -H "Content-Type: application/json" \
    -d '{"model": "ticket-classification", "messages": [{"role": "user", "content": "TASK: classify\nMeu pedido não chegou"}]}'
done
curl -s "localhost:8090/admin/calls?last=10"   # 2 chamadas: a 3ª e a 4ª não chegaram ao provider
```

Resultado: 1ª e 2ª `200`; 3ª e 4ª `429` ("Rate limit exceeded ... Current limit: 2"). Repetido dentro do mesmo minuto, todas são recusadas.

## Mapa de decisões

| ADR | Nível | Resumo |
|---|---|---|
| [0001](docs/adr/0001-gateway-litellm.md) | solução | LiteLLM Proxy self-hosted como única rota da aplicação até os modelos |
| [0002](docs/adr/0002-providers-e-teto-de-gasto.md) | corporativa | OpenAI e Anthropic aceitos, com redundância obrigatória; teto de US$ 100 por 30 dias para a IA do helpdesk |
| [0003](docs/adr/0003-capacidades-logicas.md) | solução | uma capacidade lógica por feature; modelos físicos só no gateway |
| [0004](docs/adr/0004-politica-de-fallback.md) | solução | fallback técnico em toda capacidade; modelo fraco aceito em F1, F2 e F3 e recusado em F4, com evidência |
| [0005](docs/adr/0005-timeout-e-retry.md) | software | tentativas e timeout por destino no gateway, prazo total na feature, nenhum retry no SDK |
| [0006](docs/adr/0006-governanca-de-chaves.md) | solução | virtual keys por consumidor, com orçamento e limite declarados em `gateway/keys.yaml` e aplicados na subida |
| [0007](docs/adr/0007-banco-do-gateway.md) | solução | Postgres (`gateway-db`) para as virtual keys e o gasto por chave |
| [0008](docs/adr/0008-modo-de-execucao.md) | solução | F1 e F4 síncronas, F2 em streaming (SSE), F3 assíncrona (Asynchronous Request-Reply), com as perguntas da árvore de decisão por feature |
| [0009](docs/adr/0009-leitura-da-metrica.md) | software | régua fixa nas três medições; `schemas` declarado estável por natureza; nenhuma outra exceção |

O plano de refatoração, com o piloto, as revisões e o handoff para o agente de IA, está em [docs/refactoring-plan.md](docs/refactoring-plan.md).
