"""`qp preflight`: valida el despliegue antes de operar.

Distinto de `qp doctor`, y la diferencia no es cosmetica:

* `doctor` responde **esta bien construida la plataforma**. Mira contratos y
  capacidades. No necesita configuracion ni plugins, y por eso funciona sobre un
  arbol recien clonado.
* `preflight` responde **puede arrancar ESTE despliegue en ESTE modo**. Resuelve
  la configuracion efectiva, inventaria los plugins instalados y comprueba las
  salvaguardas del modo solicitado.

Un despliegue puede pasar CI y fallar aqui: le falta un fichero de
configuracion, sobra un plugin incompatible o el modo pide garantias que la
maquina no ofrece. Fallar en este punto cuesta un mensaje; fallar mas adelante
cuesta una corrida entera cuyo resultado parece valido.

Codigos de salida, pensados para CI:
    0  el despliegue puede arrancar
    1  hay hallazgos que lo impiden
    2  no se pudo ejecutar la comprobacion
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.container.bootstrap import load_configuration
from app.container.plugins import discover_manifests, manifest_payloads
from app.container.preflight import Preflight, PreflightResult, environment_record
from app.core.exceptions import ConfigError
from app.core.types import Severity

ROOT = Path(__file__).resolve().parents[3]

OK, FAIL = "✓", "✗"

#: Severidad a partir de la cual un hallazgo impide arrancar. `WARNING` no
#: bloquea a proposito: un aviso que aborta el arranque se convierte, en la
#: primera urgencia, en un aviso que alguien silencia para siempre.
BLOCKING = Severity.ERROR


def _effective_config(root: Path, mode: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """Configuracion resuelta, o el motivo por el que no pudo resolverse.

    Se devuelve el fallo como dato en lugar de propagarlo: que la configuracion
    no resuelva es justamente uno de los hallazgos que preflight existe para
    reportar, y abortar aqui privaria al operador del resto del diagnostico.
    """
    try:
        return load_configuration(root, mode=mode).trace.flat(), None
    except ConfigError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _render(result: PreflightResult, plugins: int, config_error: str | None) -> list[str]:
    """Informe legible: toda comprobacion ejecutada y todo hallazgo.

    Se listan TODAS las comprobaciones, incluidas las que pasaron. Un informe
    que solo muestra fallos no permite distinguir "todo bien" de "no se
    comprobo", y esa es justo la distincion que importa en un diagnostico.

    La marca va por informe y no por comprobacion porque los codigos siguen el
    patron `PREFLIGHT_<MOTIVO>` y no llevan el nombre de la comprobacion que los
    produjo. Atribuirlos por heuristica daria una marca verde a una comprobacion
    que fallo, que es peor que no marcarla.
    """
    lines = [
        f"  {OK} plugins inventariados: {plugins}",
        f"  {FAIL} configuracion: {config_error}"
        if config_error is not None
        else f"  {OK} configuracion resuelta",
    ]
    lines += [f"  {OK if result.ok else FAIL} {n.replace('_', ' ')}" for n in result.checks_run]

    findings = [
        f"      {issue.severity.name:<8} {issue.code}: {issue.message}" for issue in result.report
    ]
    if findings:
        lines.append("")
        lines.extend(findings)
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp preflight", description=__doc__)
    parser.add_argument("--mode", default="research", help="modo de ejecucion a validar")
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifests = discover_manifests(ROOT)
    config, config_error = _effective_config(ROOT, args.mode)

    try:
        result = Preflight(ROOT, mode=args.mode).run(
            effective_config=config,
            installed_plugins=manifest_payloads(manifests),
        )
    except tomllib.TOMLDecodeError as exc:
        sys.stderr.write(f"No se pudo leer un contrato: {exc}\n")
        return 2

    unreadable = [m for m in manifests if not m.ok]
    blocking = result.report.of_severity(BLOCKING)
    blocked = bool(blocking) or config_error is not None or bool(unreadable)

    if args.json:
        payload = {
            "preflight": result.to_dict(),
            "config": {"resolved": config is not None, "error": config_error},
            "plugins": [m.to_dict() for m in manifests],
            "environment": environment_record(),
            "ok": not blocked,
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 1 if blocked else 0

    out = sys.stdout
    out.write(f"\nQuantPlatform preflight  [modo {args.mode}]\n\n")
    out.write("\n".join(_render(result, len(manifests), config_error)) + "\n")

    for manifest in unreadable:
        out.write(f"  {FAIL} plugin {manifest.name}: {manifest.error}\n")

    out.write("\n")
    if not blocked:
        out.write(f"Preflight OK - {len(result.checks_run)} comprobaciones\n\n")
        return 0

    out.write(f"Preflight FALLO - {len(result.report)} hallazgos\n\n")
    return 1


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
