"""`qp download`: descarga un historico, lo persiste y lo registra.

    qp download --symbol EURUSD --timeframe M15 --from 2018-01-01 --to 2026-01-01

No contiene logica: traduce argumentos y delega en `HistoricalStorageService`.
La composicion la hace la raiz -`build_download_service`-, que es el unico sitio
autorizado a conocer implementaciones concretas.

La superficie de argumentos se fija completa desde el primer commit aunque parte
solo se ejercite mas adelante: Discovery y walk-forward pediran rangos, y cambiar
la firma de un comando publicado despues cuesta mas que preverla ahora.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.application.download.runner import PROVIDERS, build_download_service
from app.container.bootstrap import SystemClock
from app.core.exceptions import PlatformError
from app.core.types import NS_PER_SECOND, Symbol, Timeframe, TimestampNs

ROOT = Path(__file__).resolve().parents[3]

#: Formato de fecha aceptado en `--from` y `--to`. Solo dia, y en UTC: una hora
#: local en la linea de comandos haria que la misma orden descargase rangos
#: distintos segun la maquina.
DATE_FORMAT = "%Y-%m-%d"


def _timestamp(value: str) -> TimestampNs:
    """Convierte una fecha `YYYY-MM-DD` a nanosegundos UTC."""
    try:
        moment = datetime.strptime(value, DATE_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Fecha invalida {value!r}: se espera {DATE_FORMAT}"
        ) from exc
    return TimestampNs(int(moment.timestamp()) * NS_PER_SECOND)


def _timeframe(value: str) -> Timeframe:
    try:
        return Timeframe(value.upper())
    except ValueError as exc:
        available = ", ".join(sorted(item.value for item in Timeframe))
        raise argparse.ArgumentTypeError(
            f"Marco temporal invalido {value!r}. Validos: {available}"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qp download",
        description="Descarga un historico, lo valida, lo escribe y lo registra",
    )
    parser.add_argument("--symbol", required=True, help="instrumento, p.ej. EURUSD")
    parser.add_argument(
        "--timeframe", required=True, type=_timeframe, help="marco temporal, p.ej. M15"
    )
    parser.add_argument("--from", dest="start", type=_timestamp, help="fecha inicial UTC")
    parser.add_argument("--to", dest="end", type=_timestamp, help="fecha final UTC")
    parser.add_argument(
        "--broker",
        default="MT5",
        choices=PROVIDERS,
        help="proveedor del historico (por defecto MT5)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="sobrescribe el historico si ya existe",
    )
    parser.add_argument("--root", type=Path, default=ROOT / "data", help="raiz de datos")
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Codigos de salida: 0 correcto, 1 fallo del comando, 2 uso incorrecto."""
    args = _parser().parse_args(list(argv or []))

    try:
        service = build_download_service(args.root, clock=SystemClock(), provider=args.broker)
        report = service.download(
            Symbol(args.symbol),
            args.timeframe,
            start_ns=args.start,
            end_ns=args.end,
            overwrite=args.overwrite,
        )
    except PlatformError as error:
        # Se captura `PlatformError` y no `Exception`: atrapa cualquier fallo
        # previsto por la plataforma -fuente caida, destino ocupado, serie
        # invalida- y deja pasar los errores de programacion, que no deben
        # presentarse como si fueran una condicion de negocio.
        if args.json:
            sys.stdout.write(json.dumps({"ok": False, "error": error.to_dict()}, indent=2))
            return 1
        sys.stderr.write(f"{error}\n")
        return 1

    if args.json:
        sys.stdout.write(json.dumps({"ok": True, **report.to_dict()}, indent=2, default=str))
        return 0

    out = sys.stdout
    out.write(f"\n{report.symbol} {report.timeframe}  <-  {report.provider}\n\n")
    out.write(f"  barras      {report.bar_count}\n")
    out.write(f"  rango       {report.first_ns} .. {report.last_ns}\n")
    out.write(f"  huella      {report.fingerprint}\n")
    out.write(f"  escrito en  {report.write.physical_location}\n")
    out.write(f"  tamano      {report.write.bytes_written} bytes\n\n")
    return 0


__all__ = ["main"]
