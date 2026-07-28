"""`qp plugins`: inventario y validacion de los bloques que entran al catalogo.

    qp plugins list      que hay instalado, interno y externo
    qp plugins validate  aplica configs/plugins.toml a lo instalado

`validate` NO importa nada. Comprueba los manifiestos y solo los manifiestos,
que es lo que permite rechazar un plugin incompatible antes de que su codigo se
ejecute. Un plugin que reclama un nombre ya tomado o una API futura debe
rechazarse sin haber corrido su `__init__`; si se importara primero y se
validara despues, el rechazo llegaria cuando el efecto ya ocurrio.

La validacion se delega en `Preflight`, que es quien aplica el contrato. Aqui no
hay ni una regla propia: duplicarlas produciria dos listas de requisitos que
acabarian discrepando, y entonces `qp plugins validate` diria que si mientras el
arranque dice que no.

Se llama `plugins_cmd.py` por el mismo motivo que `config_cmd.py`: evitar dos
modulos con el mismo nombre corto en un arbol sin imports relativos.

Codigos de salida:
    0  todo lo instalado cumple el contrato
    1  hay plugins que no pueden cargarse
    2  uso incorrecto
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.container.plugins import (
    PluginManifest,
    discover_manifests,
    external_directories,
    internal_packages,
    manifest_payloads,
    plugin_contract,
)
from app.container.preflight import Preflight
from app.core.types import Severity

ROOT = Path(__file__).resolve().parents[3]

OK, FAIL = "✓", "✗"

#: Prefijo de los hallazgos que produce la comprobacion de plugins. Se filtra
#: por el para no mezclar en `qp plugins` los hallazgos de arquitectura o de
#: modo, que tienen su propio comando.
PLUGIN_CODE_PREFIX = "PREFLIGHT_PLUGIN_"


def _list(manifests: Sequence[PluginManifest], *, as_json: bool) -> int:
    internal = internal_packages(ROOT)
    directories = external_directories(ROOT)

    if as_json:
        payload = {
            "internal_packages": list(internal),
            "external_directories": [str(d) for d in directories],
            "plugins": [m.to_dict() for m in manifests],
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0

    out = sys.stdout
    out.write("\nCatalogos internos\n")
    for dotted in internal:
        # `-` y no `✓`: que el paquete este declarado no significa que aporte
        # bloques. Marcarlo verde antes de la fase 4 daria por poblado un
        # catalogo vacio.
        out.write(f"  - {dotted}\n")

    out.write("\nDirectorios de plugins\n")
    for directory in directories:
        state = "presente" if directory.is_dir() else "ausente"
        out.write(f"  {directory.name:<20} {state}\n")

    out.write(f"\nPlugins instalados ({len(manifests)})\n")
    if not manifests:
        out.write("  ninguno\n")
    for manifest in manifests:
        mark = OK if manifest.ok else FAIL
        kind = str(manifest.data.get("kind", "?"))
        version = str(manifest.data.get("version", "?"))
        out.write(f"  {mark} {manifest.name:<24} {kind:<16} v{version}\n")
        if not manifest.ok:
            out.write(f"      {manifest.error}\n")
    out.write("\n")
    return 0


def _validate(manifests: Sequence[PluginManifest], *, mode: str, as_json: bool) -> int:
    unreadable = [m for m in manifests if not m.ok]
    result = Preflight(ROOT, mode=mode).run(installed_plugins=manifest_payloads(manifests))
    findings = [i for i in result.report if i.code.startswith(PLUGIN_CODE_PREFIX)]
    blocking = [i for i in findings if i.severity >= Severity.ERROR]
    ok = not blocking and not unreadable

    if as_json:
        payload = {
            "ok": ok,
            "n_plugins": len(manifests),
            "unreadable": [m.to_dict() for m in unreadable],
            "findings": [i.to_dict() for i in findings],
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if ok else 1

    out = sys.stdout
    contract = plugin_contract(ROOT)
    required = contract.get("manifest", {}).get("required", ())
    out.write(f"\nContrato: api {contract.get('meta', {}).get('api_version', '?')}, ")
    out.write(f"{len(required)} campos obligatorios\n")
    out.write(f"Instalados: {len(manifests)}\n\n")

    for manifest in unreadable:
        out.write(f"  {FAIL} {manifest.name}: {manifest.error}\n")
    for issue in findings:
        out.write(f"  {FAIL} {issue.severity.name:<8} {issue.code}: {issue.message}\n")

    if ok:
        out.write("  todos los plugins cumplen el contrato\n\n")
        return 0
    out.write("\n")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp plugins", description=__doc__)
    parser.add_argument("action", choices=("list", "validate"))
    parser.add_argument("--mode", default="research", help="modo de ejecucion")
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifests = discover_manifests(ROOT)
    if args.action == "list":
        return _list(manifests, as_json=args.json)
    return _validate(manifests, mode=args.mode, as_json=args.json)


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
