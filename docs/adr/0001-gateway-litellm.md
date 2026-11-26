# 0001. Colocar um AI Gateway self-hosted (LiteLLM Proxy) entre a aplicação e os providers

- Status: aceita
- Data: 2026-11-24
- Nível: solução

## Contexto

Na v1, cada feature chamava o SDK do provider de dentro do código. A credencial, o nome do modelo e o formato da API ficavam espalhados em `config.py` e `llm.py` ("Diagnóstico da v1"). Isso trouxe três consequências medidas:

- trocar um modelo exigiu editar código e reconstruir a imagem: 111 s de deploy (Dor 4);
- um provider fora do ar parou metade do helpdesk, mesmo com o modelo equivalente saudável no outro provider (Dor 3);
- `llm` e `config` estavam na zona de dor da métrica, com D 0,80 e 1,00.

O enunciado exige um gateway self-hosted no compose (serviço `gateway`), com capacidades lógicas, credenciais fora da aplicação, troca de modelo por configuração, governança e resiliência.

## Opções consideradas

1. **LiteLLM Proxy.** API no formato da OpenAI para qualquer provider, virtual keys com orçamento e limite, retries, fallbacks entre grupos de modelos, preço por modelo configurável. O README do `provider-fake` traz uma configuração testada com a imagem `v1.102.1`.
2. **Outro gateway open source** (Portkey Gateway, Envoy AI Gateway). Teriam as mesmas capacidades com mais trabalho de integração, e sem uma configuração já testada contra o provider simulado.
3. **Biblioteca dentro da aplicação** (o SDK do LiteLLM em processo). Resolve o formato e o fallback, mas mantém as credenciais e a governança dentro do `app`. Isso contraria o requisito de credenciais fora da aplicação.

## Decisão

LiteLLM Proxy, imagem `ghcr.io/berriai/litellm:v1.102.1`, como serviço `gateway` no compose. É a única rota da aplicação até os modelos:

- O `app` fala com o gateway pelo SDK `openai`, no formato da OpenAI, qualquer que seja o provider por trás. O único componente que faz isso é `app/helpdesk/gateway.py`, e só `main.py` o importa.
- A configuração é versionada em `gateway/`: `config.yaml` (destinos, preços, timeouts, retries, fallbacks) e `keys.yaml` (virtual keys).
- As chaves dos providers chegam só ao serviço `gateway`. O `app` recebe apenas `GATEWAY_URL` e a sua virtual key. Nenhum serviço usa `env_file`: cada um recebe no `compose.yaml` só as variáveis de que precisa. O `provider-fake`, por exemplo, recebe só as duas chaves que aceita, e não a master key nem as virtual keys.

## Consequências

- Melhor: credencial, escolha do modelo, custo, limites e resiliência ficam num lugar só. A aplicação pede uma capacidade (ADR 0003), e trocar o modelo físico é editar `gateway/config.yaml` e reiniciar só o `gateway`.
- Pior: mais um componente para operar. A imagem tem ~1,6 GB, e a subida com banco leva de ~1,5 a 3,5 min. O gateway é um ponto único de falha no compose (uma réplica). As virtual keys exigem um banco (ADR 0007).
- Passa a ser necessário declarar o preço de cada modelo simulado. Sem isso, o LiteLLM calcula custo zero e nenhum orçamento estoura.

## Evidência

```sh
grep -rE "gpt-fake|claude-fake" app/                                         # vazio
grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/  # vazio
docker compose exec app env | grep -E "GATEWAY|FAKE"                         # só GATEWAY_URL e GATEWAY_API_KEY
grep -rnE "^(from|import) (openai|anthropic)" app/helpdesk                   # só gateway.py
```

Depois de qualquer chamada pela borda, `/admin/calls` registra a chamada no modelo físico configurado no gateway. A seção "Troca de modelo" do README mostra a troca sem reiniciar o `app`.
