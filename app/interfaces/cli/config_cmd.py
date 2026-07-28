"""`qp config`: muestra y valida la configuracion efectiva.

    qp config show      valor efectivo de cada clave, con su procedencia
    qp config validate  falla si la resolucion produce errores

`show` imprime la procedencia y no solo el valor. Es la diferencia entre
responder "que vale esta clave" y responder "por que vale eso y no lo que yo
puse", y la segunda es la pregunta que se hace de verdad al depurar. La traza
conserva los candidatos descartados justamente para poder contestarla sin
reejecutar nada.

Se llama `config_cmd.py` y no `config.py` porque `app.config` es el paquete de
proveedores: dos modulos con el mismo nombre corto en un arbol que prohibe
imports relativos es una ambiguedad esperando a ocurrir.

Codigos de salida:
    0  la configuracion resuelve y es valida
    1  hay errores de resolucion o claves obligatorias ausentes
    2  uso incorrecto
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.container.bootstrap import ResolvedConfig, load_configuration
from app.core.config.resolver import require
from app.core.exceptions import ConfigError

ROOT = Path(__file__).resolve().parents[3]


def _resolve(mode: str, assignments: tuple[str, ...]) -> tuple[ResolvedConfig | None, str | None]:
    """Configuracion resuelta, o el motivo del fallo como dato.

    El fallo no se propaga porque `qp config validate` existe precisamente para
    reportarlo: una excepcion sin capturar daria una traza de Python donde debe
    haber un diagnostico.
    """
    try:
        return load_configuration(ROOT, mode=mode, cli_assignments=assignments), None
    except ConfigError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _show(config: ResolvedConfig, *, as_json: bool) -> int:
    if as_json:
        payload = {
            "fingerprint": str(config.fingerprint),
            "values": config.trace.to_dict(),
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0

    out = sys.stdout
    summary = config.describe()
    out.write(f"\nhuella  {summary['fingerprint']}\n")
    out.write(f"claves  {summary['keys']}\n\n")
    out.write(config.trace.report() + "\n")

    contested = summary["contested"]
    if contested:
        # Donde hay desacuerdo entre ficheros es donde se esconden las sorpresas,
        # asi que se repite aparte en lugar de dejarlo dentro del listado largo.
        out.write(f"\nClaves disputadas por mas de una fuente: {', '.join(contested)}\n")
    out.write("\n")
    return 0


def _validate(config: ResolvedConfig, required: tuple[str, ...], *, as_json: bool) -> int:
    """Comprueba que existan las claves obligatorias que se pidan.

    La lista de obligatorias es un argumento y no una constante porque depende
    del caso de uso: un backtest necesita `data.path` y una corrida de discovery
    no. Fijarla aqui obligaria a un esquema unico para todos los modos.
    """
    report = require(config.trace, required)

    if as_json:
        payload = {
            "ok": report.ok,
            "fingerprint": str(config.fingerprint),
            "report": report.to_dict(),
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if report.ok else 1

    out = sys.stdout
    out.write(f"\nhuella  {config.fingerprint}\n")
    if report.ok:
        out.write(f"Configuracion valida: {len(config.trace)} claves resueltas\n\n")
        return 0

    out.write(f"Configuracion invalida: {len(report)} hallazgos\n\n")
    for issue in report:
        out.write(f"  {issue.severity.name:<8} {issue.code}: {issue.message}\n")
    out.write("\n")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp config", description=__doc__)
    parser.add_argument("action", choices=("show", "validate"))
    parser.add_argument("--mode", default="research", help="modo de ejecucion")
    parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="CLAVE=VALOR",
        help="fija una clave, repetible; maxima prioridad",
    )
    parser.add_argument(
        "--require",
        dest="required",
        action="append",
        default=[],
        metavar="CLAVE",
        help="clave obligatoria a comprobar, repetible (solo con validate)",
    )
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    args = parser.parse_args(list(argv) if argv is not None else None)

    config, error = _resolve(args.mode, tuple(args.assignments))
    if config is None:
        sys.stderr.write(f"No se pudo resolver la configuracion: {error}\n")
        return 1

    if args.action == "show":
        return _show(config, as_json=args.json)
    return _validate(config, tuple(args.required), as_json=args.json)


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
