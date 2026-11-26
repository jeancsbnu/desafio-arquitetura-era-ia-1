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

O app abre a conexão com o gateway já na subida (`GET /models` com a sua chave, o que também passa pela autenticação do gateway). Sem isso, a 1ª requisição depois de reiniciar o `app` levava 1,35 s até o 1º trecho, perto do limite.

**O gateway também se aquece na subida.** A 1ª chamada real de um processo do LiteLLM paga uma inicialização única, que o `/admin/calls` mostrou não ser do provider: ele levou sempre 1.076 ms. Os headers `x-litellm-*` mostraram que também não é o overhead do LiteLLM (4 a 57 ms), e sim o caminho da chamada HTTP.

O custo é pago uma vez por processo, qualquer que seja o destino. Depois de reiniciar o gateway, a 1ª chamada à F4 levou 1,55 a 2,28 s, e a F1 (outro destino) e a F2 (outro provider), logo em seguida, já saíram em ~1,15 s.

Sem aquecimento, a 1ª F1 depois de reiniciar o gateway levou de 1,64 a 4,76 s nas minhas 5 amostras, e de 1,7 a 2,8 s nas 5 da revisão independente. Isso é ~0,5 a 3,7 s a mais que a chamada normal de ~1,1 s. Só 1 das 10 amostras passou do limite de 3 s (4,76 s), mas outras chegaram perto. A margem depende da carga da máquina, e por isso o aquecimento virou parte da subida, e não um detalhe de desempenho.

Formas avaliadas, e a escolhida:
- **Resposta simulada do LiteLLM (`mock_response`)**, por uma chave interna autorizada. Não chega ao provider, mas pula justamente a chamada HTTP, que é o que custa. Medido: a 1ª F1 foi de 1,60 s para 1,55 s. Descartada.
- **Endpoints que não chamam modelo** (`/health/readiness`, `/models`, `/key/info`), ou um `start_period` maior no healthcheck. Não percorrem o caminho da chamada; o `/models` já é o que o app usa. Descartados.
- **Chamadas reais ao provider na subida.** Aqueceriam tudo, mas apareceriam no `/admin/calls`, que é o instrumento usado para contar tentativas e conferir modelos. Descartadas.
- **Destino apontando para um caminho do provider que não é de inferência** (`/warmup`, que responde `404` sem registro). Medido: aquecimento parcial, com a 1ª F1 em 1,44 s, porque a resposta de erro não percorre a parte de sucesso (leitura da resposta, custo, registro de gasto). Substituída pela opção seguinte.
- **Escolhida: um servidor local dentro do container do gateway.** O `bootstrap_keys.py` o sobe por alguns segundos, e ele responde no formato da OpenAI. O destino interno `gateway-warmup` aponta para ele. A 1ª chamada percorre o caminho completo do gateway, inclusive o de sucesso, sem chegar ao provider. Medido:
  - depois de reiniciar o gateway, 1ª F1 em 1,12 a 1,17 s (3 amostras);
  - depois de `down` + `up`, 1ª F1 em 1,14 s e 1º trecho da F2 em 0,93 s;
  - 0 chamadas em `/admin/calls` depois da subida.

  Limites:
  - a chave do app não enxerga esse destino;
  - no registro de gasto do gateway fica uma linha de custo 0 por subida, feita com a master key;
  - se o aquecimento falhar, inclusive com a porta local 4099 ocupada ou uma resposta inválida, ele só registra no log e o gateway sobe do mesmo jeito. A 1ª versão não cobria esses casos: a revisão independente do PR #1 apontou que eles impediriam o gateway de ficar saudável, e a correção foi testada simulando cada um.

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
