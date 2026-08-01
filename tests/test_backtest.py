"""Simulacion historica: reglas de posicion, llenados y el bucle.

Dos tests sostienen el fichero, y los dos verifican que el motor NO cuenta las
mentiras clasicas de un backtest:

`test_an_ambiguous_bar_resolves_against_the_strategy` -- cuando el stop y el
objetivo caen dentro de la misma vela, el OHLC no dice cual se toco primero.
Suponer el objetivo produce curvas espectaculares y falsas que no fallan en
ninguna parte, porque el dato que las desmentiria no existe.

`test_an_order_never_fills_on_the_bar_that_decided_it` -- la decision se toma al
cierre de `i` y se llena en la apertura de `i+1`. Llenar en la misma barra es
usar informacion que en vivo no se tiene.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, Symbol, Timeframe, TimestampNs
from app.domain.entities.bars import Bars
from app.domain.entities.order import OrderIntent, OrderType
from app.domain.entities.trade import ExitReason, Position
from app.domain.services.positions import (
    close_position,
    observe_bar,
    protective_levels,
    resolve_bar_exit,
    trade_costs,
    unrealized_pnl,
)
from app.domain.value_objects.instrument import CostModel, Instrument
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec
from app.execution.fills import AdverseFillSimulator, OpenFillSimulator
from app.portfolio.limits import RiskLimits
from app.portfolio.policy import FixedFractionalRiskPolicy
from app.research.backtest import BacktestEngine, BacktestResult, as_port_result
from app.research.features.frame import build_frame
from app.research.strategies import compile_strategy
from app.shared.ports import BacktestEnginePort, ExecutionSimulatorPort

STEP = Timeframe.M15.nanoseconds
AT = TimestampNs(1_700_000_000_000_000_000)


def _instrument(**overrides: Any) -> Instrument:
    base: dict[str, Any] = {
        "symbol": Symbol("EURUSD"),
        "point": 0.00001,
        "digits": 5,
        "contract_size": 100_000.0,
        "value_per_point_per_lot": 1.0,
        "min_lot": 0.01,
        "max_lot": 100.0,
        "lot_step": 0.01,
        "margin_per_lot": 0.0,
        "costs": CostModel(spread_points=10.0, slippage_points=5.0),
    }
    base.update(overrides)
    return Instrument(**base)


def _position(direction: Direction = Direction.LONG, **overrides: Any) -> Position:
    base: dict[str, Any] = {
        "symbol": Symbol("EURUSD"),
        "direction": direction,
        "lots": 1.0,
        "entry_price": 1.1000,
        "entry_time": AT,
    }
    base.update(overrides)
    return Position(**base)


def _engine(simulator: ExecutionSimulatorPort | None = None) -> BacktestEngine:
    return BacktestEngine(
        simulator=simulator if simulator is not None else OpenFillSimulator(),
        risk=FixedFractionalRiskPolicy(
            RiskLimits(risk_fraction=0.01, absolute_min_stop_points=1.0, min_stop_cost_multiple=0.0)
        ),
    )


def _bars(count: int = 900, *, seed: int = 5) -> Bars:
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0.0, 0.0006, count))
    opening = close - rng.normal(0.0, 0.0001, count)
    margin = np.abs(rng.normal(0.0, 0.0004, count)) + 0.00005
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, count * STEP, STEP, dtype=np.int64),
        open=opening,
        high=np.maximum(opening, close) + margin,
        low=np.minimum(opening, close) - margin,
        close=close,
        volume=np.full(count, 100.0),
    )


def _spec(**risk: Any) -> StrategySpec:
    return StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={"fast": 8, "slow": 20}),),  # type: ignore[arg-type]
        exits=(BlockSpec(name="ema_exit", params={"period": 10}),),  # type: ignore[arg-type]
        risk=risk,
    )


# ---------------------------------------------------------------------------
# La barra ambigua
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_an_ambiguous_bar_resolves_against_the_strategy() -> None:
    """El OHLC no puede decir cual se toco primero: haria falta el tick.

    Resolver a favor del objetivo produce curvas de equity espectaculares y
    completamente falsas, y no falla en ninguna parte.
    """
    posicion = _position(stop_loss=1.0950, take_profit=1.1050)

    salida = resolve_bar_exit(posicion, bar_high=1.1060, bar_low=1.0940)

    assert salida is not None
    assert salida.reason is ExitReason.STOP_LOSS
    assert salida.price == 1.0950
    assert salida.ambiguous is True


@pytest.mark.unit
def test_an_unambiguous_bar_is_not_marked_as_ambiguous() -> None:
    """Contar como ambigua una barra que no lo es inflaria la sospecha y haria
    inutil la propia medida."""
    posicion = _position(stop_loss=1.0950, take_profit=1.1050)

    solo_objetivo = resolve_bar_exit(posicion, bar_high=1.1060, bar_low=1.0990)
    solo_stop = resolve_bar_exit(posicion, bar_high=1.1010, bar_low=1.0940)

    assert solo_objetivo is not None and solo_objetivo.reason is ExitReason.TAKE_PROFIT
    assert not solo_objetivo.ambiguous
    assert solo_stop is not None and solo_stop.reason is ExitReason.STOP_LOSS
    assert not solo_stop.ambiguous


@pytest.mark.unit
def test_a_short_reads_its_levels_the_other_way_round() -> None:
    corta = _position(Direction.SHORT, stop_loss=1.1050, take_profit=1.0950)

    assert resolve_bar_exit(corta, bar_high=1.1060, bar_low=1.0990) is not None
    assert resolve_bar_exit(corta, bar_high=1.1060, bar_low=1.0990).reason is ExitReason.STOP_LOSS  # type: ignore[union-attr]
    objetivo = resolve_bar_exit(corta, bar_high=1.1010, bar_low=1.0940)
    assert objetivo is not None and objetivo.reason is ExitReason.TAKE_PROFIT


@pytest.mark.unit
def test_a_bar_that_touches_nothing_closes_nothing() -> None:
    posicion = _position(stop_loss=1.0950, take_profit=1.1050)

    assert resolve_bar_exit(posicion, bar_high=1.1010, bar_low=1.0990) is None


@pytest.mark.unit
def test_a_position_without_levels_is_never_closed_by_the_bar() -> None:
    assert resolve_bar_exit(_position(), bar_high=2.0, bar_low=0.5) is None


@pytest.mark.integration
def test_the_engine_counts_the_bars_it_had_to_guess() -> None:
    """Una estrategia con muchas barras ambiguas depende de una suposicion que
    ningun dato sostiene, y sin contarlas esa fragilidad no aparece."""
    resultado = _engine().run(
        spec=_spec(stop_atr_multiple=0.5, target_atr_multiple=0.5),
        bars=_bars(),
        instrument=_instrument(),
        initial_equity=10_000.0,
    )

    assert resultado.ambiguous_bars > 0, "con stop y objetivo estrechos tiene que haberlas"
    assert resultado.ambiguous_bars <= len(resultado.trades)


# ---------------------------------------------------------------------------
# Causalidad de la ejecucion
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_an_order_never_fills_on_the_bar_that_decided_it() -> None:
    """La decision se toma al CIERRE de `i`; llenar en `i` usaria informacion
    que en vivo no se tiene todavia.

    Se comprueba sobre el dato: el precio de entrada de cada operacion tiene que
    ser la APERTURA de una barra posterior a la que decidio.
    """
    bars = _bars()
    resultado = _engine().run(
        spec=_spec(), bars=bars, instrument=_instrument(), initial_equity=10_000.0
    )
    instrumento = _instrument()
    aperturas = np.asarray(bars.open)
    tiempos = np.asarray(bars.timestamp)

    # La decision que origino cada orden, para poder contrastar de que barra
    # salio. Sin esto el test solo comprobaria que la entrada cae en ALGUNA
    # apertura, y llenar en la misma barra que decide tambien cumple eso.
    compilada = compile_strategy(_spec())
    decision = compilada.evaluate(
        build_frame(bars, (*compilada.feature_requests, ("atr", {"period": 14})))
    )

    assert resultado.trades
    for trade in resultado.trades:
        indice = int(np.searchsorted(tiempos, int(trade.entry_time)))
        # Redondeado a la precision cotizable: no se transacciona a un precio
        # que el broker no cotiza.
        esperado = instrumento.round_price(float(aperturas[indice]))
        assert trade.entry_price == pytest.approx(esperado, abs=1e-9), (
            "la entrada no ocurrio en la apertura de su barra"
        )
        assert indice >= 1, "no hay barra anterior de la que pudiera venir la decision"
        assert int(decision.direction[indice - 1]) == int(trade.direction), (
            "la orden no salio del CIERRE de la barra anterior: llenar en la misma "
            "barra que decide usa informacion que en vivo no se tiene"
        )


@pytest.mark.integration
def test_truncating_the_series_does_not_rewrite_past_trades() -> None:
    """La version incremental de la causalidad, que es como corre en vivo."""
    bars = _bars()
    engine = _engine()
    completa = engine.run(
        spec=_spec(), bars=bars, instrument=_instrument(), initial_equity=10_000.0
    )
    corte = 600
    parcial = engine.run(
        spec=_spec(), bars=bars.slice(0, corte), instrument=_instrument(), initial_equity=10_000.0
    )

    limite = int(np.asarray(bars.timestamp)[corte - 1])
    previas = [t for t in completa.trades if int(t.exit_time) < limite]

    assert len(parcial.trades) >= len(previas)
    for antes, despues in zip(previas, parcial.trades, strict=False):
        assert antes.entry_price == pytest.approx(despues.entry_price)
        assert antes.exit_price == pytest.approx(despues.exit_price)


# ---------------------------------------------------------------------------
# Niveles y excursiones
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_stop_goes_below_a_long_and_above_a_short() -> None:
    instrumento = _instrument()

    largo, _ = protective_levels(
        direction=Direction.LONG,
        entry_price=1.1000,
        stop_distance=0.0050,
        instrument=instrumento,
    )
    corto, _ = protective_levels(
        direction=Direction.SHORT,
        entry_price=1.1000,
        stop_distance=0.0050,
        instrument=instrumento,
    )

    assert largo == pytest.approx(1.0950)
    assert corto == pytest.approx(1.1050)


@pytest.mark.unit
def test_the_levels_are_rounded_to_what_the_broker_accepts() -> None:
    """Un stop con mas decimales de los admitidos lo reajusta el broker, y
    entonces el nivel simulado y el real dejan de coincidir sin aviso."""
    stop, target = protective_levels(
        direction=Direction.LONG,
        entry_price=1.1000,
        stop_distance=0.001234567,
        instrument=_instrument(),
        target_distance=0.002345678,
    )

    assert stop == round(stop, 5)
    assert target is not None and target == round(target, 5)


@pytest.mark.unit
@pytest.mark.parametrize("distancia", [0.0, -0.001])
def test_a_non_positive_stop_distance_is_rejected(distancia: float) -> None:
    """Sin proteccion no hay posicion que abrir; aceptarlo la dejaria desnuda."""
    with pytest.raises(InvariantViolation):
        protective_levels(
            direction=Direction.LONG,
            entry_price=1.1,
            stop_distance=distancia,
            instrument=_instrument(),
        )


@pytest.mark.unit
def test_excursions_track_the_intrabar_path_and_not_the_closes() -> None:
    """Una posicion que aguanto un retroceso del 80% del stop y acabo ganando es
    afortunada, y medirla por cierres la haria parecer comoda."""
    posicion = _position()

    tras_una = observe_bar(posicion, bar_high=1.1050, bar_low=1.0900)
    tras_dos = observe_bar(tras_una, bar_high=1.1100, bar_low=1.0950)

    assert tras_dos.mae_price == pytest.approx(1.0900), "el peor no puede mejorar"
    assert tras_dos.mfe_price == pytest.approx(1.1100)
    assert tras_dos.bars_held == 2


@pytest.mark.unit
def test_a_short_reverses_which_excursion_is_adverse() -> None:
    corta = observe_bar(_position(Direction.SHORT), bar_high=1.1050, bar_low=1.0900)

    assert corta.mae_price == pytest.approx(1.1050)
    assert corta.mfe_price == pytest.approx(1.0900)


@pytest.mark.unit
def test_observing_a_bar_does_not_touch_the_original_position() -> None:
    original = _position()

    observe_bar(original, bar_high=1.2, bar_low=1.0)

    assert original.bars_held == 0
    assert original.mae_price is None


# ---------------------------------------------------------------------------
# Costes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_commission_and_slippage_are_paid_twice_and_the_spread_once() -> None:
    """El spread se paga al cruzar, no al deshacer."""
    instrumento = _instrument(
        costs=CostModel(commission_per_lot=3.5, spread_points=10.0, slippage_points=2.0)
    )
    comision, spread, slippage, financiacion = trade_costs(
        position=_position(lots=2.0), exit_time=AT, instrument=instrumento
    )

    assert comision == pytest.approx(3.5 * 2.0 * 2)
    assert spread == pytest.approx(10.0 * 1.0 * 2.0)
    assert slippage == pytest.approx(2.0 * 1.0 * 2.0 * 2)
    assert financiacion == 0.0


@pytest.mark.unit
def test_financing_scales_with_the_days_held() -> None:
    instrumento = _instrument(costs=CostModel(financing_per_lot_per_day=-1.5))
    dos_dias = TimestampNs(int(AT) + 2 * 86_400_000_000_000)

    _, _, _, financiacion = trade_costs(
        position=_position(lots=1.0), exit_time=dos_dias, instrument=instrumento
    )

    assert financiacion == pytest.approx(-3.0), "un carry a favor es un resultado legitimo"


@pytest.mark.unit
def test_gross_is_the_theoretical_result_and_net_pays_the_market() -> None:
    """La diferencia entre los dos revela una estrategia cuyo edge desaparece al
    pagar el mercado; si el llenado ya trajera los costes dentro, seria cero."""
    instrumento = _instrument(costs=CostModel(commission_per_lot=5.0, spread_points=20.0))
    operacion = close_position(
        _position(lots=1.0),
        exit_price=1.1100,
        exit_time=AT,
        reason=ExitReason.TAKE_PROFIT,
        instrument=instrumento,
    )

    assert operacion.gross_pnl == pytest.approx(1000.0)  # 1000 puntos x 1 x 1 lote
    assert operacion.total_cost > 0.0
    assert operacion.net_pnl == pytest.approx(operacion.gross_pnl - operacion.total_cost)


@pytest.mark.unit
def test_excursions_are_reported_in_account_money() -> None:
    """Viven en `Trade` junto a `gross_pnl`: mezclar unidades en el mismo objeto
    invita a compararlos sin convertir."""
    instrumento = _instrument(value_per_point_per_lot=10.0)
    posicion = observe_bar(_position(lots=1.0), bar_high=1.1050, bar_low=1.0900)

    operacion = close_position(
        posicion,
        exit_price=1.1000,
        exit_time=AT,
        reason=ExitReason.SIGNAL_EXIT,
        instrument=instrumento,
    )

    # 0.0100 de recorrido son 1000 puntos; 0.0050 son 500.
    assert operacion.mae == pytest.approx(-1000.0 * 10.0)
    assert operacion.mfe == pytest.approx(500.0 * 10.0)


# ---------------------------------------------------------------------------
# Llenados
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_market_order_fills_at_the_open() -> None:
    intento = OrderIntent(symbol=Symbol("EURUSD"), direction=Direction.LONG, lots=1.0)

    precio = OpenFillSimulator().fill_price(
        intent=intento,
        instrument=_instrument(),
        bar_open=1.1000,
        bar_high=1.1050,
        bar_low=1.0950,
        atr=0.0005,
    )

    assert precio == pytest.approx(1.1000)


@pytest.mark.unit
def test_the_adverse_model_fills_at_the_worst_price_of_the_bar() -> None:
    """No pretende ser realista: pretende ser una cota."""
    largo = OrderIntent(symbol=Symbol("EURUSD"), direction=Direction.LONG, lots=1.0)
    corto = OrderIntent(symbol=Symbol("EURUSD"), direction=Direction.SHORT, lots=1.0)
    argumentos: dict[str, Any] = {
        "instrument": _instrument(),
        "bar_open": 1.1000,
        "bar_high": 1.1050,
        "bar_low": 1.0950,
        "atr": 0.0005,
    }

    assert AdverseFillSimulator().fill_price(intent=largo, **argumentos) == pytest.approx(1.1050)
    assert AdverseFillSimulator().fill_price(intent=corto, **argumentos) == pytest.approx(1.0950)


@pytest.mark.unit
def test_a_stop_order_that_gapped_fills_at_the_open_and_not_at_its_level() -> None:
    """El mercado nunca estuvo en el nivel: suponer que si es optimismo."""
    intento = OrderIntent(
        symbol=Symbol("EURUSD"),
        direction=Direction.LONG,
        lots=1.0,
        order_type=OrderType.STOP,
        limit_price=1.1000,
    )

    precio = OpenFillSimulator().fill_price(
        intent=intento,
        instrument=_instrument(),
        bar_open=1.1030,
        bar_high=1.1050,
        bar_low=1.1010,
        atr=0.0005,
    )

    assert precio == pytest.approx(1.1030)


@pytest.mark.unit
def test_a_pending_order_the_bar_never_reached_is_rejected() -> None:
    intento = OrderIntent(
        symbol=Symbol("EURUSD"),
        direction=Direction.LONG,
        lots=1.0,
        order_type=OrderType.LIMIT,
        limit_price=1.0500,
    )

    with pytest.raises(InvariantViolation):
        OpenFillSimulator().fill_price(
            intent=intento,
            instrument=_instrument(),
            bar_open=1.1000,
            bar_high=1.1050,
            bar_low=1.0950,
            atr=0.0,
        )


@pytest.mark.unit
@pytest.mark.parametrize("modelo", [OpenFillSimulator(), AdverseFillSimulator()])
def test_the_fill_models_satisfy_the_port(modelo: object) -> None:
    assert isinstance(modelo, ExecutionSimulatorPort)


@pytest.mark.integration
def test_the_adverse_model_never_helps() -> None:
    """La diferencia entre los dos modelos mide cuanto del edge vive de suponer
    llenados favorables."""
    bars = _bars()
    argumentos: dict[str, Any] = {
        "spec": _spec(),
        "bars": bars,
        "instrument": _instrument(),
        "initial_equity": 10_000.0,
    }

    optimista = _engine(OpenFillSimulator()).run(**argumentos)
    pesimista = _engine(AdverseFillSimulator()).run(**argumentos)

    assert pesimista.net_profit <= optimista.net_profit + 1e-6


# ---------------------------------------------------------------------------
# El bucle
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_engine_satisfies_its_port() -> None:
    assert isinstance(_engine(), BacktestEnginePort)


@pytest.mark.integration
def test_the_port_projection_gives_trades_and_equity() -> None:
    resultado = _engine().run(
        spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=10_000.0
    )
    trades, equity = as_port_result(resultado)

    assert len(trades) == len(resultado.trades)
    assert equity.size == 900


@pytest.mark.integration
def test_equity_is_marked_to_market_every_bar() -> None:
    """Sin marcar a mercado, el drawdown solo aparece al cerrar y una estrategia
    que aguanta perdidas enormes pareceria comoda hasta el final."""
    resultado = _engine().run(
        spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=10_000.0
    )

    assert resultado.equity.size == 900
    assert np.all(np.isfinite(resultado.equity))
    # Con posiciones abiertas la curva se mueve entre cierres de operacion.
    assert np.count_nonzero(np.diff(resultado.equity)) > len(resultado.trades)


@pytest.mark.integration
def test_only_one_position_is_open_at_a_time() -> None:
    """No hay piramidacion: una senal en la misma direccion no anade."""
    resultado = _engine().run(
        spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=10_000.0
    )
    ordenadas = sorted(resultado.trades, key=lambda t: int(t.entry_time))

    for antes, despues in pairwise(ordenadas):
        assert int(despues.entry_time) >= int(antes.exit_time), "dos posiciones solapadas"


@pytest.mark.integration
def test_a_repeated_signal_never_replaces_the_open_position() -> None:
    """Sustituirla en silencio la perderia SIN registrar operacion.

    Comprobar solapamientos no lo detecta -la vieja simplemente desaparece- asi
    que se contrasta el instante de entrada: la primera operacion tiene que
    nacer de la PRIMERA senal, no de una posterior que la habria pisado.

    Se usa `rsi_reversion` y no `ema_cross` porque los cruces de medias ALTERNAN
    por construccion -tras uno al alza el siguiente es a la baja- y por tanto
    nunca repiten direccion estando ya posicionados: sobre esa estrategia la
    rama de piramidacion es inalcanzable y el test no comprobaria nada. Medido
    sobre estas mismas barras: ema_cross da 0 senales consecutivas en la misma
    direccion y rsi_reversion da 38.
    """
    bars = _bars(count=1200)
    sin_salidas = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="rsi_reversion", params={"period": 14}),),  # type: ignore[arg-type]
        risk={"stop_atr_multiple": 20.0},
    )
    resultado = _engine().run(
        spec=sin_salidas, bars=bars, instrument=_instrument(), initial_equity=10_000.0
    )
    compilada = compile_strategy(sin_salidas)
    decision = compilada.evaluate(
        build_frame(bars, (*compilada.feature_requests, ("atr", {"period": 14})))
    )

    primera_senal = int(np.flatnonzero(decision.direction != 0)[0])
    assert resultado.trades
    assert int(resultado.trades[0].entry_time) == int(np.asarray(bars.timestamp)[primera_senal + 1])

    # Lo que de verdad distingue "no piramidar" de "cerrar y reabrir en la misma
    # direccion": toda salida por reversion tiene que ir seguida de una entrada
    # CONTRARIA. Una reversion que no revierte es una contradiccion, y es
    # exactamente lo que produce tratar una senal repetida como si fuera nueva.
    ordenadas = sorted(resultado.trades, key=lambda t: int(t.entry_time))
    reversiones = 0
    for antes, despues in pairwise(ordenadas):
        if antes.exit_reason is not ExitReason.SIGNAL_REVERSE:
            continue
        reversiones += 1
        assert despues.direction is not antes.direction, (
            "una reversion abrio en la MISMA direccion: la senal repetida se "
            "trato como nueva en vez de ignorarse"
        )
    assert reversiones > 0, "sin reversiones el test no comprueba nada"


@pytest.mark.integration
def test_an_opposite_signal_reverses_the_position() -> None:
    """Sin bloque de salida, un cambio de direccion tiene que cerrar y abrir.

    Se prueba SIN salidas a proposito: con un bloque de salida activo la salida
    por senal se adelanta y cierra antes, de modo que la reversion nunca llega a
    ocurrir. Las dos rutas son economicamente iguales -se cierra y se abre lo
    contrario- pero solo una lleva la etiqueta `SIGNAL_REVERSE`, y probar la
    reversion sobre una estrategia con salidas comprobaria la otra.
    """
    sin_salidas = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={"fast": 8, "slow": 20}),),  # type: ignore[arg-type]
    )

    resultado = _engine().run(
        spec=sin_salidas, bars=_bars(count=2000), instrument=_instrument(), initial_equity=10_000.0
    )

    assert ExitReason.SIGNAL_REVERSE.value in resultado.exit_reasons()


@pytest.mark.integration
def test_nothing_stays_open_when_the_history_runs_out() -> None:
    """Contar el no realizado como beneficio premiaria a quien no cierra.

    Se busca una serie que TERMINE con posicion abierta: sobre una que cierra
    sola, eliminar el cierre final no cambiaria nada y el test no comprobaria
    nada.
    """
    sin_salidas = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={"fast": 8, "slow": 20}),),  # type: ignore[arg-type]
        risk={"stop_atr_multiple": 20.0},  # stop lejisimos: no cierra por proteccion
    )
    resultado = _engine().run(
        spec=sin_salidas, bars=_bars(count=1200), instrument=_instrument(), initial_equity=10_000.0
    )
    finales = [t for t in resultado.trades if t.exit_reason is ExitReason.END_OF_DATA]

    assert len(finales) == 1, "la posicion viva al acabar la historia tiene que cerrarse"
    assert resultado.final_equity == pytest.approx(
        resultado.initial_equity + sum(t.net_pnl for t in resultado.trades)
    )


@pytest.mark.integration
def test_the_run_is_deterministic() -> None:
    argumentos: dict[str, Any] = {
        "spec": _spec(),
        "bars": _bars(),
        "instrument": _instrument(),
        "initial_equity": 10_000.0,
    }
    primera = _engine().run(**argumentos)

    for _ in range(3):
        otra = _engine().run(**argumentos)
        assert otra.to_dict() == primera.to_dict()
        np.testing.assert_array_equal(otra.equity, primera.equity)


@pytest.mark.integration
def test_the_seed_changes_nothing_because_nothing_is_sampled() -> None:
    argumentos: dict[str, Any] = {
        "spec": _spec(),
        "bars": _bars(),
        "instrument": _instrument(),
        "initial_equity": 10_000.0,
    }

    assert (
        _engine().run(**argumentos, seed=1).to_dict()
        == _engine().run(**argumentos, seed=999).to_dict()
    )


@pytest.mark.integration
def test_a_risk_policy_that_refuses_produces_no_trades() -> None:
    """El motor no puede saltarse la politica de riesgo."""
    imposible = BacktestEngine(
        simulator=OpenFillSimulator(),
        risk=FixedFractionalRiskPolicy(RiskLimits(absolute_min_stop_points=1e9)),
    )

    resultado = imposible.run(
        spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=10_000.0
    )

    assert resultado.trades == ()
    assert resultado.rejected_by_risk > 0
    assert resultado.final_equity == pytest.approx(10_000.0)


@pytest.mark.integration
@pytest.mark.parametrize("equity", [0.0, -100.0])
def test_a_non_positive_initial_equity_is_rejected(equity: float) -> None:
    with pytest.raises(InvariantViolation):
        _engine().run(spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=equity)


@pytest.mark.integration
def test_a_series_shorter_than_the_warmup_is_rejected() -> None:
    """Mejor decirlo que devolver una corrida vacia que parece un resultado."""
    with pytest.raises(InvariantViolation, match="calentamiento"):
        _engine().run(
            spec=_spec(), bars=_bars(count=15), instrument=_instrument(), initial_equity=10_000.0
        )


@pytest.mark.integration
def test_the_result_says_what_to_distrust() -> None:
    resultado: BacktestResult = _engine().run(
        spec=_spec(), bars=_bars(), instrument=_instrument(), initial_equity=10_000.0
    )
    volcado = resultado.to_dict()

    assert set(volcado) >= {"trades", "ambiguous_bars", "rejected_by_risk", "exit_reasons"}
    assert sum(volcado["exit_reasons"].values()) == volcado["trades"]


@pytest.mark.unit
def test_unrealized_pnl_moves_with_the_price() -> None:
    posicion = _position(lots=2.0)
    instrumento = _instrument()

    assert unrealized_pnl(posicion, price=1.1000, instrument=instrumento) == pytest.approx(0.0)
    assert unrealized_pnl(posicion, price=1.1010, instrument=instrumento) == pytest.approx(200.0)
    assert unrealized_pnl(posicion, price=1.0990, instrument=instrumento) == pytest.approx(-200.0)
