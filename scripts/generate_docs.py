"""Genera los artefactos derivados de documentacion desde los contratos.

Implementa P7: la documentacion es una vista, no una fuente. `docs/ARCHITECTURE.md`
no se edita nunca a mano; se regenera desde `configs/*.toml`, `decisions/*.toml` y
`CONSTITUTION.md`.

Uso:

    python scripts/generate_docs.py            # escribe los artefactos
    python scripts/generate_docs.py --check    # falla si el disco esta desfasado

El modo `--check` es lo que hace cumplir el principio. Sin el, "la documentacion
se genera" seria una costumbre; con el, una edicion manual rompe el build en la
siguiente ejecucion. Lo usa `tests/test_governance.py`.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
DECISIONS = ROOT / "decisions"
CONSTITUTION = ROOT / "CONSTITUTION.md"
TARGET = ROOT / "docs" / "ARCHITECTURE.md"

BANNER = """<!-- ARCHIVO GENERADO. NO EDITAR. -->
<!-- Fuente: CONSTITUTION.md + configs/*.toml + decisions/*.toml -->
<!-- Regenerar: python scripts/generate_docs.py -->
"""


def _load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def principles() -> dict[str, str]:
    """Identificador -> titulo, extraidos de los encabezados de la Constitucion."""
    if not CONSTITUTION.exists():
        return {}
    pattern = re.compile(r"^##\s+(P\d+)\s+—\s+(.+)$", re.MULTILINE)
    return {
        match.group(1): match.group(2).strip()
        for match in pattern.finditer(CONSTITUTION.read_text(encoding="utf-8"))
    }


def _table(header: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_sin entradas_\n"
    line = "| " + " | ".join(header) + " |\n"
    line += "|" + "|".join("---" for _ in header) + "|\n"
    for row in rows:
        line += "| " + " | ".join(row) + " |\n"
    return line


def render() -> str:
    architecture = _load(CONFIGS / "architecture.toml")
    runtime = _load(CONFIGS / "runtime.toml")
    plugins = _load(CONFIGS / "plugins.toml")
    packages: dict[str, dict[str, Any]] = architecture.get("packages", {})
    capabilities: dict[str, dict[str, Any]] = architecture.get("capabilities", {})
    ranks: dict[str, int] = architecture.get("layer_order", {})
    laws = principles()

    out: list[str] = [BANNER, "# Arquitectura\n"]
    out.append(
        "Vista derivada de los contratos. Si algo de aqui contradice a "
        "`configs/`, el contrato tiene razon (P10).\n"
    )

    # -- constitucion -------------------------------------------------------
    out.append("\n## Principios\n")
    out.append(
        _table(
            ["Id", "Principio"],
            [[pid, title] for pid, title in sorted(laws.items(), key=lambda kv: int(kv[0][1:]))],
        )
    )
    out.append(f"\nTexto completo en [`CONSTITUTION.md`](../CONSTITUTION.md).\n")

    # -- capacidades --------------------------------------------------------
    out.append("\n## Capacidades\n")
    out.append(
        _table(
            ["Capacidad", "Responsable", "Proposito", "Paquetes"],
            [
                [
                    name,
                    str(spec.get("owner", "")),
                    str(spec.get("purpose", "")),
                    ", ".join(
                        f"`{p}`"
                        for p, ps in sorted(packages.items())
                        if ps.get("capability") == name
                    ),
                ]
                for name, spec in sorted(capabilities.items())
            ],
        )
    )

    # -- paquetes -----------------------------------------------------------
    out.append("\n## Paquetes\n")
    out.append(
        _table(
            ["Paquete", "Capa", "Capacidad", "Visibilidad", "Proposito"],
            [
                [
                    f"`app/{name}`",
                    str(spec.get("layer", "")),
                    str(spec.get("capability", "")),
                    str(spec.get("visibility", "")),
                    str(spec.get("purpose", "")),
                ]
                for name, spec in sorted(
                    packages.items(),
                    key=lambda kv: (ranks.get(str(kv[1].get("layer")), 0), kv[0]),
                )
            ],
        )
    )

    # -- dependencias -------------------------------------------------------
    out.append("\n## Matriz de dependencias\n")
    out.append(
        _table(
            ["Paquete", "Puede importar de"],
            [
                [
                    f"`{name}`",
                    ", ".join(f"`{d}`" for d in spec.get("depends", [])) or "_nada_",
                ]
                for name, spec in sorted(
                    packages.items(),
                    key=lambda kv: (ranks.get(str(kv[1].get("layer")), 0), kv[0]),
                )
            ],
        )
    )
    out.append(
        "\nVerificado en `tests/test_architecture.py`. Toda arista no declarada "
        "rompe el build.\n"
    )

    # -- justificaciones ----------------------------------------------------
    justified = [
        (name, str(spec["rationale"]).strip())
        for name, spec in sorted(packages.items())
        if spec.get("rationale")
    ]
    if justified:
        out.append("\n## Por que cada frontera esta donde esta\n")
        for name, rationale in justified:
            out.append(f"\n### `app/{name}`\n\n{rationale}\n")

    # -- comportamiento -----------------------------------------------------
    behaviour = dict(architecture.get("behavior", {}))
    cited = dict(behaviour.pop("principle", {}))
    reasons = dict(behaviour.pop("rationale", {}))
    out.append("\n## Contrato de comportamiento\n")
    out.append(
        _table(
            ["Garantia", "Principio", "Motivo"],
            [
                [
                    f"`{flag}`",
                    str(cited.get(flag, "")),
                    " ".join(str(reasons.get(flag, "")).split()),
                ]
                for flag, value in sorted(behaviour.items())
                if value is True
            ],
        )
    )

    # -- runtime ------------------------------------------------------------
    modes: dict[str, dict[str, Any]] = runtime.get("modes", {})
    if modes:
        out.append("\n## Modos de ejecucion\n")
        out.append(
            _table(
                ["Modo", "Hereda de", "Proposito"],
                [
                    [f"`{name}`", f"`{spec.get('inherits', '—')}`", str(spec.get("purpose", ""))]
                    for name, spec in modes.items()
                ],
            )
        )
        invariants = dict(runtime.get("invariants", {}))
        invariants.pop("principle", None)
        invariants.pop("rationale", None)
        if invariants:
            out.append("\n### Invariantes que ningun modo puede romper\n")
            citations = runtime.get("invariants", {}).get("principle", {})
            for name in sorted(invariants):
                out.append(f"- `{name}` ({citations.get(name, '')})\n")

    # -- plugins ------------------------------------------------------------
    isolation = plugins.get("isolation", {})
    if isolation:
        out.append("\n## Aislamiento de plugins\n")
        allowed = ", ".join(f"`{i}`" for i in isolation.get("allowed_imports", []))
        out.append(f"\nUn plugin solo ve: {allowed}. Nada mas.\n")

    # -- decisiones ---------------------------------------------------------
    adrs = sorted(DECISIONS.glob("ADR-*.toml"))
    if adrs:
        out.append("\n## Decisiones\n")
        rows: list[list[str]] = []
        for path in adrs:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            rows.append(
                [
                    f"[`{data['id']}`](../decisions/{path.name})",
                    str(data.get("status", "")),
                    str(data.get("date", "")),
                    str(data.get("owner", "")),
                    str(data.get("title", "")),
                ]
            )
        out.append(_table(["Id", "Estado", "Fecha", "Responsable", "Titulo"], rows))

    out.append(
        f"\n---\n\nGenerado el {datetime.now(UTC).strftime('%Y-%m-%d')} por "
        "`scripts/generate_docs.py`.\n"
    )
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera la documentacion derivada")
    parser.add_argument("--check", action="store_true", help="falla si el disco esta desfasado")
    args = parser.parse_args()

    expected = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != expected:
            sys.stderr.write(
                f"{TARGET.relative_to(ROOT)} esta desfasado respecto a los contratos.\n"
                "Regenera con: python scripts/generate_docs.py\n"
            )
            return 1
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(expected, encoding="utf-8")
    sys.stdout.write(f"escrito {TARGET.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
