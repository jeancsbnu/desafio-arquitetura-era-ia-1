"""Compara as saídas dos modelos `large` e `mini` do provider simulado, tarefa por tarefa.

É a evidência da política de fallback com modelo fraco (ADR de fallback). Chama o provider direto
(não o gateway), com a mesma entrada nos dois tamanhos, e grava um resumo em JSON.

Uso (com o compose no ar):
    python scripts/compare_models.py data/tickets.jsonl docs/adr/evidence/large-vs-mini.json

Só usa a biblioteca padrão. Leva alguns minutos: são ~20 mil chamadas, 32 em paralelo.
"""

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROVIDER = "http://localhost:8090"
OPENAI_KEY = "sk-fake-openai-0001"
ANTHROPIC_KEY = "sk-ant-fake-0001"
STEP_PREFIX = "Reforçando o ponto"


def ask(provider: str, model: str, task: str, content: str) -> str:
    if provider == "openai":
        url, headers = f"{PROVIDER}/openai/v1/chat/completions", {"Authorization": f"Bearer {OPENAI_KEY}"}
        body = {"model": model, "messages": [{"role": "user", "content": f"TASK: {task}\n{content}"}]}
    else:
        url, headers = f"{PROVIDER}/anthropic/v1/messages", {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": f"TASK: {task}\n{content}"}]}
    request = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"] if provider == "openai" else data["content"][0]["text"]


def both(pool, provider, task, contents, large, mini):
    larges = list(pool.map(lambda c: ask(provider, large, task, c), contents))
    minis = list(pool.map(lambda c: ask(provider, mini, task, c), contents))
    return larges, minis


def main() -> None:
    tickets = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
    summary = {}
    with ThreadPoolExecutor(max_workers=32) as pool:
        # classify e extract: todos os tickets
        for task in ("classify", "extract"):
            larges, minis = both(pool, "openai", task, [t["text"] for t in tickets], "gpt-fake-large", "gpt-fake-mini")
            diffs = [(t["id"], json.loads(a), json.loads(b)) for t, a, b in zip(tickets, larges, minis) if a != b]
            entry = {"tickets": len(tickets), "different": len(diffs), "examples": diffs[:5]}
            if task == "extract":
                entry["wrong_order_number"] = sum(1 for _, a, b in diffs if a["order_number"] != b["order_number"])
                entry["missing_product"] = sum(1 for _, a, b in diffs if a["product"] and not b["product"])
                entry["large_with_product"] = sum(1 for a in larges if json.loads(a)["product"])
            summary[task] = entry
            print(task, {k: v for k, v in entry.items() if k != "examples"}, flush=True)

        # topics: os mesmos 34 lotes de 150 que a F3 monta para o mês inteiro
        batches = ["\n".join(f"[{t['id']}] {t['text']}" for t in tickets[i:i + 150]) for i in range(0, len(tickets), 150)]
        larges, minis = both(pool, "anthropic", "topics", batches, "claude-fake-large", "claude-fake-mini")
        summary["topics"] = {"batches": len(batches), "different": sum(1 for a, b in zip(larges, minis) if a != b)}
        print("topics", summary["topics"], flush=True)

        # suggest: uma amostra (cada chamada large leva ~16 s)
        sample = [t["text"] for t in tickets[::25]]
        larges, minis = both(pool, "anthropic", "suggest", sample, "claude-fake-large", "claude-fake-mini")

        def steps(text: str) -> int:  # parágrafos de conteúdo, sem abertura, fechamento e repetições
            paragraphs = text.split("\n\n")
            return sum(1 for p in paragraphs[1:-1] if not p.startswith(STEP_PREFIX))

        summary["suggest"] = {
            "sample": len(sample),
            "different": sum(1 for a, b in zip(larges, minis) if a != b),
            "avg_chars_large": round(sum(map(len, larges)) / len(larges)),
            "avg_chars_mini": round(sum(map(len, minis)) / len(minis)),
            "avg_distinct_steps_large": round(sum(map(steps, larges)) / len(larges), 2),
            "avg_distinct_steps_mini": round(sum(map(steps, minis)) / len(minis), 2),
            "example_mini": minis[0],
        }
        print("suggest", {k: v for k, v in summary["suggest"].items() if k != "example_mini"}, flush=True)

    with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
