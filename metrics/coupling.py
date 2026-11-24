"""Mede o acoplamento de `app/helpdesk/` pela régua fixa do requisito 3.

Uso:
    python metrics/coupling.py <caminho de app/helpdesk> <nome>

Grava `metrics/results/<nome>.csv` (cabeçalho `component,ca,ce,i,a,d`) e
`metrics/results/<nome>.svg` (gráfico A × I com a Main Sequence). Só usa a biblioteca padrão.

A régua:
- componente: cada módulo `.py` do pacote, inclusive em subpastas, exceto `__init__.py`,
  identificado pelo caminho com pontos relativo à raiz do pacote (ex.: `adapters.gateway`);
- dependência: um import, relativo ou absoluto, que resolve para outro componente; bibliotecas
  externas e da biblioteca padrão não contam. Vale qualquer import do módulo, inclusive dentro de
  funções. Um import que resolve para um pacote (o `__init__.py`) não conta, porque o pacote não
  é componente;
- Ca: quantos componentes importam este; Ce: quantos componentes este importa (distintos);
- I = Ce / (Ce + Ca), ou 0 quando os dois são zero;
- A = classes abstratas (com `typing.Protocol` ou `abc.ABC` entre as bases diretas) / total de
  classes do módulo, ou 0 quando o módulo não tem classes;
- D = |A + I - 1|.
Os valores são calculados com frações exatas e arredondados para 2 casas (meio para cima).
"""

import argparse
import ast
import csv
import html
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

ABSTRACT_BASES = {"typing.Protocol", "typing_extensions.Protocol", "abc.ABC"}
RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class Component:
    name: str
    package: list[str]           # pacote do módulo, para resolver imports relativos
    tree: ast.Module
    imports: set[str]
    classes: int = 0
    abstract: int = 0


def load_components(root: Path) -> dict[str, Component]:
    components = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).with_suffix("")
        if relative.name == "__init__":
            continue
        parts = list(relative.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        components[".".join(parts)] = Component(".".join(parts), parts[:-1], tree, set())
    return components


def resolve_imports(component: Component, root_package: str, names: set[str]) -> set[str]:
    """Os componentes que este módulo importa."""
    found = set()
    for node in ast.walk(component.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] != root_package:
                    continue
                # `import helpdesk.a.b` depende do componente mais específico que existir no caminho
                for end in range(len(parts), 1, -1):
                    if ".".join(parts[1:end]) in names:
                        found.add(".".join(parts[1:end]))
                        break
        elif isinstance(node, ast.ImportFrom):
            module = node.module.split(".") if node.module else []
            if node.level:
                up = node.level - 1
                if up > len(component.package):
                    continue  # sobe além da raiz do pacote
                target = component.package[:len(component.package) - up] + module
            elif module and module[0] == root_package:
                target = module[1:]
            else:
                continue  # biblioteca externa ou da biblioteca padrão
            for alias in node.names:
                submodule = ".".join(target + [alias.name])
                if alias.name != "*" and submodule in names:
                    found.add(submodule)       # from pacote import modulo
                elif ".".join(target) in names:
                    found.add(".".join(target))  # from modulo import nome
    found.discard(component.name)
    return found


def dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Subscript):  # Protocol[T]
        return dotted(node.value, aliases)
    return None


def count_classes(component: Component) -> None:
    aliases = {}
    for node in ast.walk(component.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:                      # import typing as t
                    aliases[alias.asname] = alias.name
                else:                                 # import typing / import os.path
                    first = alias.name.split(".")[0]
                    aliases[first] = first
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    for node in ast.walk(component.tree):
        if isinstance(node, ast.ClassDef):
            component.classes += 1
            bases = {dotted(base, aliases) for base in node.bases}
            if bases & ABSTRACT_BASES:
                component.abstract += 1


def round2(value: Fraction) -> Decimal:
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def measure(root: Path) -> list[dict]:
    components = load_components(root)
    names = set(components)
    for component in components.values():
        component.imports = resolve_imports(component, root.name, names)
        count_classes(component)

    rows = []
    for name in sorted(components):
        component = components[name]
        ca = sum(1 for other in components.values() if name in other.imports)
        ce = len(component.imports)
        i = Fraction(ce, ce + ca) if ce + ca else Fraction(0)
        a = Fraction(component.abstract, component.classes) if component.classes else Fraction(0)
        d = abs(a + i - 1)
        rows.append({"component": name, "ca": ca, "ce": ce, "i": round2(i), "a": round2(a), "d": round2(d)})
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["component", "ca", "ce", "i", "a", "d"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def in_pain_zone(i: Decimal, a: Decimal) -> bool:
    return a < Decimal("0.5") and i < Decimal("0.5") and abs(a + i - 1) >= Decimal("0.5")


def write_svg(rows: list[dict], title: str, path: Path) -> None:
    size, left, top, bottom, legend_w, row_h = 560, 70, 56, 60, 340, 18
    legend_x = left + size + 36
    width = legend_x + legend_w
    height = max(top + size + bottom, top + 40 + (len(rows) + 2) * row_h)

    def x(i): return left + float(i) * size
    def y(a): return top + (1 - float(a)) * size

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="28" font-size="16" font-weight="bold" fill="#111">{html.escape(title)}</text>',
        # zonas: dor (A < 0,5, I < 0,5, D >= 0,5) e inutilidade (A > 0,5, I > 0,5, D >= 0,5)
        f'<polygon points="{x(0)},{y(0)} {x(0.5)},{y(0)} {x(0)},{y(0.5)}" fill="#f8d7d3"/>',
        f'<polygon points="{x(1)},{y(1)} {x(0.5)},{y(1)} {x(1)},{y(0.5)}" fill="#e3e3f3"/>',
        f'<text x="{x(0.02)}" y="{y(0.30)}" fill="#a33">zona de dor</text>',
        f'<text x="{x(0.98)}" y="{y(0.72)}" fill="#557" text-anchor="end">zona de inutilidade</text>',
    ]
    for tick in ("0", "0.25", "0.5", "0.75", "1"):
        t = float(tick)
        out += [
            f'<line x1="{x(t)}" y1="{y(0)}" x2="{x(t)}" y2="{y(1)}" stroke="#eee"/>',
            f'<line x1="{x(0)}" y1="{y(t)}" x2="{x(1)}" y2="{y(t)}" stroke="#eee"/>',
            f'<text x="{x(t)}" y="{y(0) + 18}" text-anchor="middle" fill="#444">{tick}</text>',
            f'<text x="{x(0) - 8}" y="{y(t) + 4}" text-anchor="end" fill="#444">{tick}</text>',
        ]
    out += [
        f'<rect x="{x(0)}" y="{y(1)}" width="{size}" height="{size}" fill="none" stroke="#999"/>',
        f'<line x1="{x(0)}" y1="{y(1)}" x2="{x(1)}" y2="{y(0)}" stroke="#2a7" stroke-width="2" stroke-dasharray="6 4"/>',
        f'<text x="{x(0.52)}" y="{y(0.52) - 8}" fill="#2a7">Main Sequence (A + I = 1)</text>',
        f'<text x="{x(0.5)}" y="{y(0) + 44}" text-anchor="middle" fill="#111">I (instabilidade)</text>',
        f'<text x="18" y="{y(0.5)}" text-anchor="middle" fill="#111" transform="rotate(-90 18 {y(0.5)})">A (abstração)</text>',
    ]

    # Cada ponto (componentes com as mesmas coordenadas viram um só) recebe um número curto, e a
    # legenda ao lado traz nome, I, A e D. Números não se sobrepõem mesmo com pontos próximos.
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["i"], row["a"]), []).append(row)
    placed: list[tuple[float, float, float, float]] = []
    legend = [
        f'<text x="{legend_x}" y="{top + 4}" font-weight="bold" fill="#111">#</text>',
        f'<text x="{legend_x + 24}" y="{top + 4}" font-weight="bold" fill="#111">componente</text>',
    ]
    for col, label in ((220, "I"), (270, "A"), (320, "D")):
        legend.append(f'<text x="{legend_x + col}" y="{top + 4}" font-weight="bold" fill="#111" text-anchor="end">{label}</text>')
    line = 1
    for number, ((i, a), members) in enumerate(sorted(groups.items()), start=1):
        px, py = x(i), y(a)
        colour = "#c0392b" if in_pain_zone(i, a) else "#1f5fa8"
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{colour}"/>')
        label_w, label_h = 7 * len(str(number)) + 2, 13
        lx, ly = min(px + 5, x(1) - label_w), py - 7 - label_h
        while any(lx < ox + ow and ox < lx + label_w and ly < oy + oh and oy < ly + label_h for ox, oy, ow, oh in placed):
            ly -= label_h
        placed.append((lx, ly, label_w, label_h))
        out.append(f'<text x="{lx:.1f}" y="{ly + label_h - 2:.1f}" font-weight="bold" fill="{colour}">{number}</text>')
        for k, row in enumerate(members):
            ry = top + 4 + line * row_h
            if k == 0:
                legend.append(f'<text x="{legend_x}" y="{ry}" font-weight="bold" fill="{colour}">{number}</text>')
            legend.append(f'<text x="{legend_x + 24}" y="{ry}" fill="{colour}">{html.escape(row["component"])}</text>')
            for col, key in ((220, "i"), (270, "a"), (320, "d")):
                legend.append(f'<text x="{legend_x + col}" y="{ry}" fill="{colour}" text-anchor="end">{row[key]}</text>')
            line += 1
    legend.append(f'<text x="{legend_x}" y="{top + 4 + (line + 1) * row_h}" fill="#c0392b">em vermelho: zona de dor '
                  f'(A &lt; 0,5, I &lt; 0,5 e D ≥ 0,5)</text>')
    out += legend
    out.append("</svg>\n")
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="caminho do pacote a medir (a pasta app/helpdesk de um checkout)")
    parser.add_argument("name", help="nome do resultado, ex.: v1-coupled")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="pasta de saída (padrão: metrics/results)")
    args = parser.parse_args()

    root = args.source.resolve()
    if not root.is_dir():
        parser.error(f"pasta não encontrada: {root}")
    rows = measure(root)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.out / f"{args.name}.csv")
    write_svg(rows, f"{args.name}: A × I ({root.name}, {len(rows)} componentes)", args.out / f"{args.name}.svg")
    print(f"{'component':<28}{'ca':>4}{'ce':>4}{'i':>6}{'a':>6}{'d':>6}")
    for r in rows:
        print(f"{r['component']:<28}{r['ca']:>4}{r['ce']:>4}{r['i']:>6}{r['a']:>6}{r['d']:>6}")
    print(f"-> {args.out / (args.name + '.csv')} e .svg")


if __name__ == "__main__":
    main()
