"""`qp backtest`: evalua una estrategia declarada sobre una serie catalogada.

    qp backtest --strategy configs/strategies/ema_cross_m15.toml --dataset <huella>

No contiene logica: traduce argumentos, pide a la raiz de composicion lo que solo
ella puede construir -el reloj, la configuracion efectiva y el catalogo de
instrumentos- y delega en `BacktestApplicationService`.

Se pide la HUELLA del dataset y no el par simbolo/marco temporal. Dos descargas
del mismo simbolo pueden diferir -un reproceso, un rango distinto- y una corrida
que solo declarase "EURUSD M15" no seria reconstruible: la identidad de una serie
es su contenido. La huella la imprime `qp download --json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.application.backtest import (
    FILL_MODELS,
    BacktestReport,
    BacktestRequest,
    build_backtest_service,
)
from app.container.bootstrap import load_strategy_spec, platform_services
from app.core.exceptions import PlatformError

ROOT = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qp backtest",
        description="Simula una estrategia bar a bar y deja constancia auditable",
    )
    parser.add_argument(
        "--strategy", required=True, type=Path, help="fichero .toml con la composicion"
    )
    parser.add_argument(
        "--dataset", required=True, help="huella de contenido de la serie en el catalogo"
    )
    # Sin defecto en el parser, a proposito. El capital de partida lo declara
    # `configs/backtest.toml`, y esa configuracion no se puede resolver hasta
    # conocer `--root`, que es otro argumento. Un numero aqui volveria a crear la
    # segunda fuente que ADR-0015 retiro.
    parser.add_argument(
        "--equity",
        type=float,
        default=None,
        help="capital inicial (por defecto, el de configs/backtest.toml)",
    )
    parser.add_argument("--seed", type=int, default=0, help="semilla maestra de la corrida")
    parser.add_argument(
        "--fills",
        default="OPEN",
        choices=FILL_MODELS,
        help="modelo de llenado: OPEN es realista, ADVERSE es pesimista",
    )
    parser.add_argument("--label", default="", help="etiqueta libre para reconocer la corrida")
    parser.add_argument("--root", type=Path, default=ROOT, help="raiz del proyecto")
    parser.add_argument("--data", type=Path, default=None, help="raiz de historicos")
    parser.add_argument("--json", action="store_true", help="salida legible por maquina")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Codigos de salida: 0 correcto, 1 fallo del comando, 2 uso incorrecto."""
    args = _parser().parse_args(list(argv or []))

    try:
        # Modo `deterministic` -el defecto de `platform_services`-: fija el
        # reloj, de modo que dos corridas iguales producen el MISMO `run_id` y
        # por tanto el mismo artefacto, byte a byte. Con el reloj de pared cada
        # ejecucion escribiria en una carpeta nueva y la reproducibilidad seria
        # indemostrable (P1).
        platform = platform_services(args.root)
        service = build_backtest_service(
            args.root,
            clock=platform.clock,
            instruments=platform.instruments,
            config_hash=platform.config_hash,
            limits=platform.risk_limits,
            data_root=args.data,
            fill_model=args.fills,
        )
        report = service.run(
            BacktestRequest(
                spec=load_strategy_spec(args.strategy),
                dataset_fingerprint=args.dataset,
                initial_equity=(
                    args.equity if args.equity is not None else platform.initial_equity
                ),
                seed=args.seed,
                label=args.label,
            )
        )
    except PlatformError as error:
        # Igual que en `qp download`: se captura `PlatformError` y no `Exception`.
        # Atrapa lo que la plataforma preve -estrategia ilegible, serie ausente,
        # instrumento no declarado, calentamiento insuficiente- y deja pasar los
        # errores de programacion, que no deben presentarse como si fueran una
        # condicion de negocio.
        if args.json:
            sys.stdout.write(json.dumps({"ok": False, "error": error.to_dict()}, indent=2))
            return 1
        sys.stderr.write(f"{error}\n")
        return 1

    if args.json:
        sys.stdout.write(json.dumps({"ok": True, **report.to_dict()}, indent=2, default=str))
        return 0

    _render(report)
    return 0


def _render(report: BacktestReport) -> None:
    """Vuelca el informe en texto. Presentacion, nada mas.

    Se imprimen `barras ambiguas` y `rechazos de riesgo` junto a las metricas y
    no en una seccion de detalle: son las dos cifras que dicen cuanto hay que
    desconfiar del numero de arriba, y esconderlas bajo un `--verbose` seria
    presentar el resultado sin su advertencia.
    """
    metrics = report.metrics
    out = sys.stdout
    out.write(f"\nBACKTEST  {report.strategy_id}\n\n")
    out.write(f"  corrida       {report.run_id}\n")
    out.write(f"  dataset       {report.dataset_fingerprint}\n")
    out.write(f"  operaciones   {report.n_trades}\n")
    out.write(f"  capital final {report.final_equity:.2f}\n\n")
    out.write(f"  neto          {metrics.net_profit:.2f}\n")
    out.write(f"  acierto       {metrics.win_rate:.1%}\n")
    out.write(f"  drawdown      {metrics.max_drawdown:.2f} ({metrics.max_drawdown_pct:.1%})\n")
    out.write(f"  sharpe        {metrics.sharpe:.2f}\n")
    out.write(f"  degenerado    {'si' if metrics.is_degenerate else 'no'}\n\n")
    out.write(f"  barras ambiguas    {report.ambiguous_bars}\n")
    out.write(f"  rechazos de riesgo {report.rejected_by_risk}\n\n")
    for artifact in report.artifacts:
        out.write(f"  -> {artifact}\n")
    out.write("\n")


__all__ = ["main"]
