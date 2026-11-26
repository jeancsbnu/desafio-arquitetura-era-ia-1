# 0008. Modo de execução por feature: F1 e F4 síncronas, F2 em streaming, F3 assíncrona

- Status: aceita
- Data: 2026-11-25
- Nível: solução

## Contexto

Na v1, as quatro features eram síncronas: o resultado vinha inteiro no corpo da resposta. A borda (nginx) encerra a requisição com `504` depois de 30 s sem nenhum byte. Medido no diagnóstico:

- a F2 deixava o atendente 16 s sem ver nada (Dor 2);
- a F3 do mês inteiro leva ~5,5 min (34 lotes de ~10 s) e morria no `504`, enquanto a aplicação seguia gastando tokens (Dor 1).

Uma chamada a LLM demora segundos, custa por token, chega aos poucos e prende conexões. A pergunta não é qual modo adotar, mas qual modo para cada caso de uso. As decisões abaixo seguem a árvore de decisão do curso, aplicada às características do contrato de cada feature:

1. Alguém está esperando agora?
2. Cabe no orçamento de latência?
3. É consumível aos poucos?

## Opções consideradas

Para cada feature: síncrono (resultado inteiro na resposta), streaming (Server-Sent Events na mesma conexão) e assíncrono (Asynchronous Request-Reply: `202`, consulta de status, resultado depois). Callback (webhook) foi descartado para todas, porque nenhum consumidor expõe uma URL de retorno.

## Decisão

### F1 Classificar ticket: síncrona

- **Alguém está esperando agora?** Sim: o cliente, na abertura do ticket, esperando a confirmação na tela.
- **Cabe no orçamento de latência?** Sim. A saída é curta e de valores fixos (11 tokens), e a resposta leva ~1,1 s no `large`, contra os 3 s exigidos.
- **É consumível aos poucos?** Não: uma categoria pela metade não serve para nada.

Síncrona, com prazo explícito de 14 s (ADR 0005). Com o primário travado, ela responde pelo fallback em ~6 s.

### F2 Sugerir resposta: streaming

- **Alguém está esperando agora?** Sim: o atendente, olhando a tela.
- **Cabe no orçamento de latência?** O texto inteiro, não: ~16 s no `large`, contra o 1º trecho em até 1,5 s. O primeiro token, sim: sai em ~0,8 s do provider.
- **É consumível aos poucos?** Sim: o atendente "começa a ler e editar assim que o texto aparece", e a saída é longa.

Streaming por Server-Sent Events (`text/event-stream`), na forma de "Contratos sugeridos":
- cada trecho vai num evento `data: {"chunk": ...}`, e o fim, no evento `end` com o `ticket_id`;
- uma falha antes do 1º trecho ainda vira status HTTP (`503` ou `422`);
- uma falha depois do 1º trecho, quando o `200` já foi enviado, vira o evento `error` com `{"ticket_id", "message"}`, e o stream termina.

Para o texto chegar aos poucos, nada no caminho pode acumular a resposta:
- o app responde com `X-Accel-Buffering: no`, que desliga o buffer do nginx da borda só nessa resposta, sem alterar `edge/`;
- o gateway repassa os trechos;
- não há middleware de compressão.

O app abre a conexão com o gateway já na subida. Sem isso, a 1ª requisição depois de reiniciar o `app` levava 1,35 s até o 1º trecho, perto do limite.

O gateway também tem um custo na 1ª chamada real de cada rota até o provider. Medido pela borda, a 1ª F1 depois da subida levou:
- 1,38 s com só o `gateway` reiniciado;
- 1,55 a 1,60 s depois de `docker compose down` + `up`;
- 2,74 s logo depois de ligar a máquina, com o Docker ainda aquecendo.

A 1ª F2, nesse último caso, teve o 1º trecho em 1,26 s. Depois disso, ~1,1 s e ~0,9 s. Tudo fica dentro dos limites (3 s e 1,5 s), mas com pouca folga logo depois de ligar a máquina. A tabela completa está no README ("A primeira chamada depois da subida").

Duas formas de aquecer o gateway foram avaliadas e descartadas:
- **Resposta simulada do LiteLLM (`mock_response`)**, enviada na subida por uma chave interna autorizada. Foi implementada e medida: não chega ao provider (0 chamadas em `/admin/calls`), mas a 1ª F1 só passou de 1,60 s para 1,55 s. A simulação pula justamente a chamada HTTP ao provider, que é o que custa na 1ª vez. A mudança foi desfeita.
- **Chamadas reais ao provider na subida.** Aqueceriam o caminho inteiro, mas apareceriam no `/admin/calls`, que é o instrumento usado para contar tentativas e conferir modelos. Um ganho de ~0,5 s não justifica poluir a prova.

Streaming muda a espera percebida, mas não a duração: a conexão fica aberta os ~16 s. É aceitável, porque há alguém lendo durante todo esse tempo, e os trechos chegam a cada ~25 ms, muito antes dos 30 s da borda.

O 1º trecho em menos de 1 s vale com o provider normal. Quando o gateway precisa de outro destino, a troca acontece antes do 1º trecho, e ele atrasa:
- ~5,2 s com o primário em `error_500`;
- ~6,0 s com o primário travado;
- ~10,4 s sem nenhum `large`.

Depois que o texto começou, não há troca de destino: uma falha no meio do stream vira o evento `error`. Isso é o que o contrato pede, porque o atendente já está lendo um texto que não pode ser emendado com o de outro modelo.

### F3 Relatório de temas: assíncrona (Asynchronous Request-Reply)

- **Alguém está esperando agora?** Não: quem pede é um painel interno no navegador, e "ninguém fica esperando olhando para ele".
- **Cabe no orçamento de latência?** Não: ~5,5 min para o mês, contra 30 s da borda e 1 s para aceitar o pedido.
- **É consumível aos poucos?** Não: o relatório só faz sentido somado; um tema contado pela metade engana.

O painel não expõe callback, então o padrão é Asynchronous Request-Reply:
- `POST /reports/topics` valida o período (erros de entrada continuam `422` síncronos), agenda a tarefa e responde `202` com `Location: /reports/topics/status/{id}` e `Retry-After: 5`;
- a URL de status responde `200` com `{"state": "pending"|"running", "progress": "1800/5000"}`, ou `{"state": "failed", "reason": ...}`;
- quando a tarefa termina, a URL de status responde `303 See Other` para `/reports/topics/{id}`, que devolve o relatório.

A tarefa roda num pool de 4 threads dentro do próprio `app` (adaptador `background.py`, porta `TaskRunner`), com o estado em memória, e as 100 últimas tarefas terminadas continuam consultáveis. **Nenhum serviço novo** entra no compose (fila, banco, worker): para o volume do helpdesk, poucos relatórios ao mesmo tempo bastam, e o enunciado deixa fora do escopo sobreviver a um reinício com tarefas em andamento.

Para que uma tarefa que não consegue terminar chegue a `failed` em até 60 s:
- cada lote tem prazo de 50 s no app;
- no gateway, cada destino tem 15 s por tentativa (até 3 destinos);
- qualquer exceção do trabalho leva a tarefa a `failed`, com o motivo. Nenhuma fica presa em `running`.

Os 60 s contam do início da execução. Um 5º pedido simultâneo espera `pending` até um dos 4 terminar. Com os providers fora, cada tarefa falha em 14 a 17 s (`error_500`) ou em ~46 s (`timeout`), então essa espera só aparece com mais de 4 relatórios pedidos ao mesmo tempo.

### F4 Extrair dados do pedido: síncrona

- **Alguém está esperando agora?** Uma automação de troca e devolução, que precisa do resultado para seguir.
- **Cabe no orçamento de latência?** Sim: ~1,1 s.
- **É consumível aos poucos?** Não: a automação precisa dos dois campos completos e validados (`#` + 6 dígitos).

Síncrona, com prazo de 14 s e falha explícita (`503`) sem modelo `large` (ADR 0004). Fazê-la assíncrona traria estado e consulta de status a uma chamada de 1 s, sem ganho para quem consome.

## Consequências

- Melhor: cada feature cumpre o seu orçamento de latência, e nenhuma requisição termina em `504` da borda, inclusive com o provider fora ou travado. O relatório do mês deixa de ser desperdiçado: a conexão não fica presa e o trabalho termina e é entregue.
- Pior:
  - a F2 mantém a conexão aberta durante toda a geração;
  - a F3 passa a ter estado (as tarefas) e um protocolo de consulta que o cliente precisa seguir;
  - o estado das tarefas vive na memória de um único processo: some num reinício e não funcionaria com várias réplicas do `app` sem um armazenamento compartilhado;
  - uma tarefa não pode ser cancelada: um relatório pedido e abandonado roda até o fim. Pedidos repetidos para o mesmo período não são deduplicados, o que o enunciado deixa fora do escopo.
- O contrato da F2 e da F3 mudou. Por isso, na `main`, a suíte de caracterização foi atualizada para o contrato novo. O conteúdo continua o mesmo: o texto concatenado da F2 e o relatório da F3 batem com os gravados da v1. Na `v1-coupled` e na `v2-decoupled`, a suíte é a original.

## Evidência

Medido pela borda (`localhost:8000`); `tests/flows/test_flows.py` repete cada item:

| Item | Resultado |
|---|---|
| F1, normal | `200` em ~1,1 s |
| F2, normal (`curl -N`) | 1º trecho em 0,85–0,95 s, mesmo logo depois de reiniciar o app; texto completo em ~17 s; `event: end` |
| F2, `anthropic` em `midstream_error` | `200`, 151 trechos e depois `event: error` / `{"ticket_id": "TK-00042", "message": "A geração foi interrompida"}`; 1 chamada no provider |
| F2, `anthropic` em `error_500` / em `timeout` | stream completo pelo `gpt-fake-large`; 1º trecho em 5,2 s / 6,0 s |
| F3, mês inteiro | `202` em 0,02–0,04 s, com `Location` e `Retry-After: 5`; status `running` com `progress` de 150 em 150 tickets; `303` aos 345 s e relatório com `total_tickets` 5000, produzido por chamadas `topics` registradas em `/admin/calls` (34 lotes de 150) |
| F3, os dois providers em `error_500` | `failed` com `reason` em 14,3 s (17 s na revisão independente) |
| F3, os dois providers em `timeout` | `failed` com `reason` em ~46 s |
| F1, `openai` em `timeout` | `200` em 6,1 s, pelo fallback |
