# 0007. Adicionar um Postgres (`gateway-db`) como banco do gateway

- Status: aceita
- Data: 2026-11-24
- Nível: solução

## Contexto

O enunciado exige que todo serviço do compose além de `app` e `gateway` tenha um ADR que o justifique a partir de um requisito. O requisito 5 pede virtual keys, uma chave com orçamento e outra com limite de requisições (ADR 0006). No LiteLLM Proxy, virtual keys e o controle de gasto por chave só existem com um banco de dados. Sem banco, o gateway aceita apenas a master key.

## Opções consideradas

1. **Sem banco, só a master key.** Não atende ao requisito de orçamento por chave.
2. **Postgres no compose.** É o banco que o LiteLLM suporta, com o schema criado pelo próprio gateway (Prisma) na subida.
3. **Banco gerenciado na nuvem.** O avaliador precisa subir tudo localmente, sem serviço externo.

## Decisão

Serviço `gateway-db` com a imagem pública `postgres:16-alpine`:

- não publica porta no host, e só o `gateway` o alcança, pela rede do compose;
- a senha vem do `.env` (`GATEWAY_DB_PASSWORD`);
- tem healthcheck com `pg_isready`, e o `gateway` só sobe com o banco saudável;
- os dados ficam no volume anônimo da imagem: sobrevivem a `restart` e a `up`, e são descartados por `docker compose down`.

## Consequências

- Melhor: virtual keys, orçamento e gasto por chave funcionam. O schema e as chaves são criados na subida, sem passo manual (ADR 0006).
- Pior: mais um serviço para operar. A primeira subida do gateway fica mais lenta (~2 a 3 min, com as migrações do Prisma). Por isso o healthcheck do `gateway` tem `start_period: 300s`, para o `docker compose up --wait` não desistir numa máquina mais lenta.
- Depois de `docker compose down`, o gasto acumulado volta a zero. As chaves voltam porque são recriadas de `gateway/keys.yaml`. Para um teto mensal real, o volume precisaria ser nomeado e ter backup, o que fica fora do escopo deste ambiente local.

## Evidência

```sh
docker compose ps gateway-db                    # healthy, sem porta publicada
docker compose logs gateway | grep bootstrap_keys
# [bootstrap_keys] chave helpdesk-app criada (demo-budget, demo-rate-limit)
curl -s localhost:4000/health/readiness         # "db": "connected"
```
