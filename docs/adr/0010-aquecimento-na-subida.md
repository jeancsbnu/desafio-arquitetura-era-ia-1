# 0010. Aquecer o app e o gateway na subida, para a 1ª requisição cumprir os prazos

- Status: aceita
- Data: 2026-11-25
- Nível: software

## Contexto

Os prazos do requisito 6 valem também para a 1ª requisição depois de uma subida: a F1 em até 3 s e o 1º trecho da F2 em até 1,5 s (ADR 0008). Com o provider normal, a F1 leva ~1,1 s e o 1º trecho da F2 ~0,9 s, mas a 1ª chamada de cada processo pagava custos de inicialização.

**No app.** Sem abrir a conexão com o gateway antes, a 1ª requisição depois de reiniciar o `app` levava 1,35 s até o 1º trecho da F2, perto do limite.

**No gateway.** A 1ª chamada real de um processo do LiteLLM paga uma inicialização única, qualquer que seja o destino:
- o `/admin/calls` mostrou que o custo não é do provider, que levou sempre 1.076 ms;
- os headers `x-litellm-*` mostraram que também não é o overhead do LiteLLM (4 a 57 ms), e sim o caminho da chamada HTTP;
- depois de reiniciar o gateway, a 1ª chamada à F4 levou 1,55 a 2,28 s, e a F1 (outro destino) e a F2 (outro provider), logo em seguida, já saíram em ~1,15 s.

Sem aquecimento, a 1ª F1 depois de reiniciar o gateway levou de 1,64 a 4,76 s nas minhas 5 amostras, e de 1,7 a 2,8 s nas 5 da revisão independente. Isso é ~0,5 a 3,7 s a mais que a chamada normal. Só 1 das 10 amostras passou do limite de 3 s (4,76 s), mas outras chegaram perto. A margem depende da carga da máquina, e por isso o aquecimento é parte da subida, e não um detalhe de desempenho.

## Opções consideradas

Para o gateway:

1. **Resposta simulada do LiteLLM (`mock_response`)**, por uma chave interna autorizada. Não chega ao provider, mas pula justamente a chamada HTTP, que é o que custa. Medido: a 1ª F1 foi de 1,60 s para 1,55 s.
2. **Endpoints que não chamam modelo** (`/health/readiness`, `/models`, `/key/info`), ou um `start_period` maior no healthcheck. Não percorrem o caminho da chamada.
3. **Chamadas reais ao provider na subida.** Aqueceriam tudo, mas apareceriam no `/admin/calls`, que é o instrumento usado para contar tentativas e conferir modelos.
4. **Destino apontando para um caminho do provider que não é de inferência** (`/warmup`, que responde `404` sem registro). Medido: aquecimento parcial, com a 1ª F1 em 1,44 s, porque a resposta de erro não percorre a parte de sucesso (leitura da resposta, custo, registro de gasto).
5. **Um servidor local dentro do container do gateway**, que responde no formato da OpenAI, e um destino interno apontando para ele.

## Decisão

**No app:** na subida (`lifespan` em `main.py`), o adaptador `gateway` lista os modelos com a chave do app (`GET /models`). Isso abre a conexão com o gateway e também passa pela autenticação dele.

**No gateway: opção 5.** O `bootstrap_keys.py`, depois de aplicar as chaves, sobe por alguns segundos um servidor local que responde no formato da OpenAI e chama o destino interno `gateway-warmup` (`gateway/config.yaml`), que aponta para ele. A 1ª chamada percorre o caminho completo do gateway, inclusive o de sucesso, sem chegar ao provider. As opções 1, 2 e 4 não aquecem o que custa (medido), e a 3 sujaria o `/admin/calls`.

O aquecimento é uma otimização e não pode impedir a subida:
- se falhar, ele registra `aquecimento falhou` no log e o gateway sobe do mesmo jeito. O arquivo de pronto (`/tmp/keys-ready`, exigido pelo healthcheck) é gravado num `finally`. Isso vale para:
  - a porta local 4099 ocupada;
  - o gateway inacessível, ou que aceita a conexão e não responde;
  - uma resposta HTTP de erro, ou uma resposta `200` que não é JSON;
  - a thread do servidor local que não inicia;
  - uma `BaseException`;
- se o gateway parar de responder, o bootstrap desiste depois de 10 s sem receber nada. O prazo do `urlopen` vale para a conexão e para cada leitura, e não para a chamada inteira: um gateway que mandasse a resposta aos poucos poderia passar disso.

## Consequências

- Melhor: a 1ª requisição depois de uma subida cumpre os prazos com folga, sem nenhuma chamada ao provider na subida.
- Pior:
  - o bootstrap ganhou um servidor HTTP local e a configuração ganhou um destino que não é capacidade (ADR 0003);
  - a chave do app não enxerga esse destino, mas no registro de gasto do gateway fica uma linha de custo 0 por subida, feita com a master key;
  - depois da subida o servidor local não existe mais, e chamar `gateway-warmup` dá erro;
  - o aquecimento do gateway não age quando só o `app` é reiniciado; nesse caso vale o aquecimento do próprio app.
- Esses limites vieram de três revisões independentes:
  - a revisão do PR #1 achou casos que impediam o gateway de ficar saudável;
  - a revisão do PR #2 achou a `BaseException` e um `shutdown()` que travava quando a thread não iniciava;
  - a revisão do PR #3 achou uma resposta HTTP de erro registrada no log como se fosse sucesso, e um teste que falhava às vezes no Linux.

## Evidência

Medido pela borda, com o aquecimento:
- depois de reiniciar o gateway, 1ª F1 em 1,12 a 1,28 s;
- depois de `down` + `up`, 1ª F1 em 1,14 a 1,35 s e 1º trecho da F2 em 0,93 a 1,04 s, com 0 chamadas em `/admin/calls` depois da subida;
- no log do gateway, `[bootstrap_keys] aquecimento: HTTP 200 em ...`.

As amostras de cada situação estão no README ("A primeira chamada depois da subida").

Cada cenário de falha é um teste em `tests/gateway/test_warmup.py`, que roda sem Docker (`python -m pytest tests/gateway`). Os testes de `main()` conferem também o outro lado: sem as chaves aplicadas, o arquivo de pronto não é gravado. Rodados contra versões anteriores do bootstrap:
- contra a do PR #2, falham 4 testes:
  - os da `BaseException`, da thread e do HTTP de erro, que são os defeitos corrigidos depois;
  - o do gateway que aceita e não responde, só porque o PR #2 tinha o prazo fixo de 10 s e o teste o reduz para 1 s;
- contra a do PR #3, falha só o do HTTP de erro.

Os testes passam no Windows e no Linux (20 execuções seguidas no WSL).
