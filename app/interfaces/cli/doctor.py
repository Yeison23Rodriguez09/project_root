"""`qp doctor`: comprueba que la plataforma este correctamente construida.

Es el comando que responde, sin ejecutar nada de trading, si el sistema esta
listo. Debe funcionar en una maquina recien clonada, sin broker, sin datos de
mercado y sin estrategias:

    pip install -e .
    qp doctor

La plataforma tiene que poder demostrar que esta bien construida ANTES de que
exista la primera estrategia. Esa inversion del orden es lo que impide repetir
el fallo del proyecto anterior, donde arquitectura y estrategias evolucionaron a
la vez y los acoplamientos resultaron imposibles de deshacer.

Codigos de salida, pensados para CI:
    0  plataforma lista
    1  hay fallos que impiden operar
    2  no se pudo ejecutar el diagnostico (falta un contrato ilegible)
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from app.container.preflight import Preflight, environment_record
from app.core.types import Severity

ROOT = Path(__file__).resolve().parents[3]

OK, FAIL, WARN, SKIP = "✓", "✗", "!", "·"


def _capability_states(root: Path) -> dict[str, dict[str, Any]]:
    """Estado declarado de cada capacidad, o vacio si no hay fichero de entrega."""
    path = root / "configs" / "delivery.toml"
    if not path.is_file():
        return {}
    return dict(tomllib.loads(path.read_text(encoding="utf-8")).get("state", {}))


def _render_preflight(result: Any) -> list[str]:
    """Una linea por comprobacion, con sus hallazgos debajo.

    Se listan TODAS las comprobaciones ejecutadas, incluidas las que pasaron. Un
    informe que solo muestra fallos no permite distinguir "todo bien" de "no se
    comprobo", y esa distincion es justo la que importa en un diagnostico.
    """
    by_check: dict[str, list[str]] = {name: [] for name in result.checks_run}
    orphan: list[str] = []
    for issue in result.report:
        # Los codigos siguen el patron PREFLIGHT_<MOTIVO>; no llevan el nombre
        # de la comprobacion, asi que se agrupan por severidad al final.
        orphan.append(f"      {issue.severity.name}: {issue.message}")

    lines: list[str] = []
    failed_codes = {i.code for i in result.report.of_severity(Severity.ERROR)}
    for name in result.checks_run:
        mark = FAIL if failed_codes and not result.ok else OK
        lines.append(f"  {mark} {name.replace('_', ' ')}")
        lines.extend(by_check[name])
    if orphan:
        lines.append("")
        lines.extend(orphan)
    return lines


def _render_capabilities(states: dict[str, dict[str, Any]]) -> list[str]:
    marks = {"ready": OK, "in_progress": WARN, "blocked": FAIL, "not_started": SKIP}
    lines: list[str] = []
    for name in sorted(states):
        spec = states[name]
        status = str(spec.get("status", "not_started"))
        phase = str(spec.get("phase", "?"))
        lines.append(f"  {marks.get(status, SKIP)} {name:<16} {status:<12} fase {phase}")
        if status == "blocked" and spec.get("blocked_by"):
            first = str(spec["blocked_by"]).strip().splitlines()[0]
            lines.append(f"      bloqueo: {first}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp doctor", description=__doc__)
    parser.add_argument("--mode", default="research", help="modo de ejecucion a validar")
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    args = parser.parse_args(argv)

    try:
        result = Preflight(ROOT, mode=args.mode).run()
    except tomllib.TOMLDecodeError as exc:
        sys.stderr.write(f"No se pudo leer un contrato: {exc}\n")
        return 2

    states = _capability_states(ROOT)

    if args.json:
        import json

        payload = {
            "preflight": result.to_dict(),
            "capabilities": states,
            "environment": environment_record(),
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if result.ok else 1

    out = sys.stdout
    out.write(f"\nQuantPlatform doctor  [modo {args.mode}]\n\n")

    out.write("Plataforma\n")
    out.write("\n".join(_render_preflight(result)) + "\n")

    if states:
        out.write("\nCapacidades\n")
        out.write("\n".join(_render_capabilities(states)) + "\n")

    environment = environment_record()
    out.write(f"\nEntorno   python {environment['python']} / {environment['platform']}\n")

    blocked = [n for n, s in states.items() if s.get("status") == "blocked"]
    ready = result.ok and not blocked

    out.write("\n")
    if ready:
        out.write("Platform READY\n\n")
        return 0

    if not result.ok:
        out.write(f"Platform NOT READY - {len(result.report)} hallazgos de gobernanza\n")
    if blocked:
        out.write(f"Capacidades bloqueadas: {', '.join(sorted(blocked))}\n")
    out.write("\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
