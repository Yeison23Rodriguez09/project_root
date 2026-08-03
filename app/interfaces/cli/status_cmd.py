"""`qp status`: estado de la plataforma, calculado.

No lee ningun fichero de estado escrito a mano. Compone el sistema y observa el
resultado. Un `platform_state.toml` mantenido a mano mentiria en cuanto alguien
olvidara actualizarlo en el commit correcto, y un informe de estado que puede
mentir es peor que no tenerlo: da confianza injustificada.

Responde a la pregunta operativa -que puede hacer hoy la plataforma- que es
distinta de la de `qp doctor` -esta bien construida-.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.container.bootstrap import platform_report

ROOT = Path(__file__).resolve().parents[3]

_MARKS = {
    "ready": "READY",
    "in_progress": "IN PROGRESS",
    "blocked": "BLOCKED",
    "not_started": "-",
}

#: Capacidades que deben estar `ready` antes de abrir Discovery. Se declaran
#: aqui y no en un documento porque es la condicion que el sistema comprueba.
_REQUIRED_FOR_DISCOVERY: tuple[str, ...] = (
    "Foundation",
    "Orchestration",
    "Governance",
    "Research",
    "Risk",
    "Execution",
    "Analytics",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    as_json = "--json" in args
    mode = "research"
    if "--mode" in args:
        index = args.index("--mode")
        if index + 1 < len(args):
            mode = args[index + 1]

    report = platform_report(ROOT, mode=mode)

    if as_json:
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0 if report.get("composed") else 1

    out = sys.stdout
    out.write(f"\nQP STATUS  [modo {mode}]\n\n")

    if not report.get("composed"):
        out.write("Plataforma......NO COMPONE\n\n")
        for error in report["errors"]:
            out.write(f"  {error}\n")
        out.write("\n")
        return 1

    container = report["container"]
    config = report["config"]
    out.write(f"Plataforma......{'READY' if not container['cycle'] else 'CICLO'}\n")
    out.write(f"  componentes   {container['registered']}\n")
    out.write(f"  arranque      {' -> '.join(container['startup_order']) or '-'}\n")
    out.write(f"  configuracion {config['keys']} claves, huella {config['fingerprint']}\n")

    capabilities = report.get("capabilities", {})
    if capabilities:
        out.write("\nCapacidades\n")
        for name, state in capabilities.items():
            out.write(f"  {name:<16}{_MARKS.get(state, state)}\n")

    missing = [name for name in _REQUIRED_FOR_DISCOVERY if capabilities.get(name) != "ready"]
    out.write("\n")
    if missing:
        out.write("Overall.........NOT READY FOR DISCOVERY\n")
        out.write(f"  falta: {', '.join(missing)}\n\n")
        return 1

    out.write("Overall.........READY FOR DISCOVERY\n\n")
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
