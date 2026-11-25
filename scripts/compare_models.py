"""Compara as saídas dos modelos `large` e `mini` do provider simulado, tarefa por tarefa.

É a evidência da política de fallback com modelo fraco (ADR 0004). Chama o provider direto (não o
gateway), com a mesma entrada nos dois tamanhos, e grava um resumo em JSON.

Uso (com o compose no ar):
    python scripts/compare_models.py data/tickets.jsonl docs/adr/evidence/large-vs-mini.json
    python scripts/compare_models.py data/tickets.jsonl docs/adr/evidence/large-vs-mini.json extract,suggest

O terceiro argumento, opcional, escolhe as tarefas (padrão: todas). As tarefas escolhidas são
recalculadas, e as outras são mantidas no JSON que já existir. Só usa a biblioteca padrão. Com
todas as tarefas, leva ~11 min: são ~20 mil chamadas, 32 em paralelo.
"""

import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROVIDER = "http://localhost:8090"
OPENAI_KEY = "sk-fake-openai-0001"
ANTHROPIC_KEY = "sk-ant-fake-0001"
TASKS = ("classify", "extract", "topics", "suggest")
REINFORCEMENT = re.compile(r"^Reforçando o ponto \d+: (.)(.*)$", re.DOTALL)
INVOICE = re.compile(r"nota fiscal (\d{6})", re.IGNORECASE)


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


def steps(text: str) -> set[str]:
    """Os passos de orientação distintos de uma sugestão: os parágrafos entre a abertura e o
    fechamento, com as repetições ("Reforçando o ponto N: ...") reduzidas ao passo original."""
    found = set()
    for paragraph in text.split("\n\n")[1:-1]:
        match = REINFORCEMENT.match(paragraph)
        found.add(match.group(1).upper() + match.group(2) if match else paragraph)
    return found


def compare_classify(pool, tickets):
    larges, minis = both(pool, "openai", "classify", [t["text"] for t in tickets], "gpt-fake-large", "gpt-fake-mini")
    diffs = [(t["id"], json.loads(a), json.loads(b)) for t, a, b in zip(tickets, larges, minis) if a != b]
    return {"tickets": len(tickets), "different": len(diffs), "examples": diffs[:5]}


def compare_extract(pool, tickets):
    larges, minis = both(pool, "openai", "extract", [t["text"] for t in tickets], "gpt-fake-large", "gpt-fake-mini")
    wrong_order = invoice = missing_product = with_product = different = 0
    examples = []
    for ticket, a, b in zip(tickets, map(json.loads, larges), map(json.loads, minis)):
        with_product += bool(a["product"])
        if a == b:
            continue
        different += 1
        if len(examples) < 5:
            examples.append((ticket["id"], a, b))
        if a["order_number"] != b["order_number"]:
            wrong_order += 1
            invoices = {f"#{n}" for n in INVOICE.findall(ticket["text"])}
            invoice += b["order_number"] in invoices
        missing_product += bool(a["product"]) and not b["product"]
    return {"tickets": len(tickets), "different": different, "wrong_order_number": wrong_order,
            "wrong_order_number_is_invoice": invoice, "large_with_product": with_product,
            "missing_product": missing_product, "examples": examples}


def compare_topics(pool, tickets):
    batches = ["\n".join(f"[{t['id']}] {t['text']}" for t in tickets[i:i + 150]) for i in range(0, len(tickets), 150)]
    larges, minis = both(pool, "anthropic", "topics", batches, "claude-fake-large", "claude-fake-mini")
    return {"batches": len(batches), "different": sum(1 for a, b in zip(larges, minis) if a != b)}


def compare_suggest(pool, tickets):
    sample = [t["text"] for t in tickets[::25]]  # amostra: cada chamada large leva ~16 s
    larges, minis = both(pool, "anthropic", "suggest", sample, "claude-fake-large", "claude-fake-mini")
    return {
        "sample": len(sample),
        "sampling": "1 a cada 25 tickets de data/tickets.jsonl",
        "different_text": sum(1 for a, b in zip(larges, minis) if a != b),
        "avg_chars_large": round(sum(map(len, larges)) / len(larges)),
        "avg_chars_mini": round(sum(map(len, minis)) / len(minis)),
        "avg_distinct_steps_large": round(sum(len(steps(a)) for a in larges) / len(larges), 2),
        "avg_distinct_steps_mini": round(sum(len(steps(b)) for b in minis) / len(minis), 2),
        "same_distinct_steps": sum(1 for a, b in zip(larges, minis) if steps(a) == steps(b)),
        "same_opening_and_closing": sum(1 for a, b in zip(larges, minis)
                                        if a.split("\n\n")[0] == b.split("\n\n")[0]
                                        and a.split("\n\n")[-1] == b.split("\n\n")[-1]),
        "example_mini": minis[0],
    }


def main() -> None:
    source, target = sys.argv[1], sys.argv[2]
    selected = sys.argv[3].split(",") if len(sys.argv) > 3 else list(TASKS)
    tickets = [json.loads(line) for line in open(source, encoding="utf-8")]
    summary = json.load(open(target, encoding="utf-8")) if os.path.exists(target) else {}
    with ThreadPoolExecutor(max_workers=32) as pool:
        for task in selected:
            summary[task] = globals()[f"compare_{task}"](pool, tickets)
            print(task, {k: v for k, v in summary[task].items() if k not in ("examples", "example_mini")}, flush=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump({task: summary[task] for task in TASKS if task in summary}, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
