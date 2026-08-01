"""Motor de simulacion historica, barra a barra y determinista.

    Bars -> FeatureFrame -> CompiledStrategy -> llenado -> posiciones -> trades

La regla que ordena todo el bucle es la causalidad declarada en ADR-0003: la
decision de la barra `i` se toma en su CIERRE, asi que la orden que produce se
llena en la apertura de `i+1`. Ejecutar en la misma barra que decide es la
segunda mentira mas comun de un backtest -despues de resolver a favor del
objetivo las barras ambiguas- y produce resultados que ninguna ejecucion real
puede reproducir.

Este motor NO contiene ninguna regla de gestion de posiciones. Donde va el
stop, cuando se considera tocado, como se acumulan excursiones y como se cierra
en un `Trade` con sus costes vive todo en `domain.services.positions`, que es
codigo compartido con el ejecutor en vivo. Aqui solo esta el bucle sobre la
historia y el marcado a mercado.

Lo que el motor recibe por puerto, y por tanto lo unico que cambiara al operar
de verdad:

    ExecutionSimulatorPort  a que precio se llena una orden
    RiskPolicyPort          cuantos lotes. `research` no puede importar
                            `portfolio` -son hermanos en la matriz- y la
                            direccion es la correcta: el motor no debe conocer
                            la politica de riesgo, solo consumirla.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, FloatArray, TimestampNs
from app.domain.entities.bars import Bars
from app.domain.entities.feature_frame import feature_key
from app.domain.entities.order import OrderIntent
from app.domain.entities.trade import ExitReason, Position, Trade
from app.domain.services.positions import (
    close_position,
    observe_bar,
    protective_levels,
    resolve_bar_exit,
    unrealized_pnl,
)
from app.domain.value_objects.instrument import Instrument
from app.domain.value_objects.strategy_spec import StrategySpec
from app.research.features.frame import FeatureRequest, build_frame
from app.research.strategies.compiler import CompiledStrategy, StrategyDecision, compile_strategy
from app.shared.ports import ExecutionSimulatorPort, RiskPolicyPort

#: Defaults de `configs/backtest.toml`. Lo que declare `StrategySpec.risk` manda.
DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_MULTIPLE = 2.0
DEFAULT_TARGET_MULTIPLE = 0.0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Todo lo que la corrida produjo, con lo necesario para desconfiar de ello.

    Attributes:
        trades: Operaciones cerradas, en orden cronologico.
        equity: Capital marcado a mercado en el CIERRE de cada barra. Marcar a
            mercado y no solo al cerrar operaciones es lo que hace que el
            drawdown aparezca cuando ocurre: una estrategia que aguanta perdidas
            enormes sin cerrar pareceria comoda hasta el final.
        ambiguous_bars: Barras en que stop y objetivo cayeron los dos dentro del
            rango y hubo que suponer cual se toco primero. No es una curiosidad:
            es la medida de cuanto del resultado descansa en una suposicion que
            el dato no sostiene.
        rejected_by_risk: Senales que la politica de riesgo no dejo dimensionar.
    """

    trades: tuple[Trade, ...]
    equity: FloatArray
    initial_equity: float
    ambiguous_bars: int
    rejected_by_risk: int
    warmup: int

    @property
    def final_equity(self) -> float:
        return float(self.equity[-1]) if self.equity.size else self.initial_equity

    @property
    def net_profit(self) -> float:
        return self.final_equity - self.initial_equity

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": len(self.trades),
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "net_profit": self.net_profit,
            "ambiguous_bars": self.ambiguous_bars,
            "rejected_by_risk": self.rejected_by_risk,
            "warmup": self.warmup,
            "exit_reasons": self.exit_reasons(),
        }

    def exit_reasons(self) -> dict[str, int]:
        """Distribucion de motivos de salida.

        `ExitReason` existe como campo de primera clase precisamente para esto:
        si el 90% de las salidas son `STOP_LOSS`, la logica de salida no esta
        aportando nada.
        """
        counts: dict[str, int] = {}
        for trade in self.trades:
            key = str(trade.exit_reason)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))


class BacktestEngine:
    """Recorre la historia barra a barra aplicando una estrategia compilada."""

    def __init__(
        self,
        *,
        simulator: ExecutionSimulatorPort,
        risk: RiskPolicyPort,
    ) -> None:
        self._simulator = simulator
        self._risk = risk

    def run(
        self,
        *,
        spec: StrategySpec,
        bars: Bars,
        instrument: Instrument,
        initial_equity: float,
        seed: int = 0,
    ) -> BacktestResult:
        """Cumple `BacktestEnginePort` y devuelve ademas el detalle auditable.

        `seed` no se usa: el recorrido es completamente determinista y no hay
        nada que muestrear. Se acepta porque el puerto lo declara, y aceptarlo
        sin usarlo es preferible a que el puerto mienta sobre sus argumentos.

        Raises:
            InvariantViolation: capital inicial no positivo, o la serie no
                alcanza el calentamiento de la estrategia.
        """
        del seed
        if initial_equity <= 0.0:
            raise InvariantViolation("El capital inicial debe ser positivo", equity=initial_equity)

        compiled = compile_strategy(spec)
        atr_period, stop_multiple, target_multiple = _risk_params(spec.risk)
        atr_name = feature_key("atr", {"period": atr_period})

        requests: tuple[FeatureRequest, ...] = (
            *compiled.feature_requests,
            ("atr", {"period": atr_period}),
        )
        frame = build_frame(bars, requests)
        if len(bars) <= frame.warmup:
            raise InvariantViolation(
                "La serie no alcanza el calentamiento",
                bars=len(bars),
                warmup=frame.warmup,
            )

        return _Run(
            engine=self,
            compiled=compiled,
            decision=compiled.evaluate(frame),
            bars=bars,
            atr=np.asarray(frame[atr_name], dtype=np.float64),
            instrument=instrument,
            initial_equity=initial_equity,
            stop_multiple=stop_multiple,
            target_multiple=target_multiple,
        ).execute()

    # -- fachada usada por `_Run` --------------------------------------------

    @property
    def simulator(self) -> ExecutionSimulatorPort:
        return self._simulator

    @property
    def risk(self) -> RiskPolicyPort:
        return self._risk


def _risk_params(risk: Mapping[str, Any]) -> tuple[int, float, float]:
    """Lee el perfil de riesgo del spec, con los defaults de configuracion."""
    return (
        int(risk.get("stop_atr_period", DEFAULT_ATR_PERIOD)),
        float(risk.get("stop_atr_multiple", DEFAULT_ATR_MULTIPLE)),
        float(risk.get("target_atr_multiple", DEFAULT_TARGET_MULTIPLE)),
    )


@dataclass(slots=True)
class _Run:
    """Estado de UNA corrida. Vive solo dentro de `BacktestEngine.run`.

    Es mutable porque un recorrido barra a barra es un proceso con estado, y
    fingir lo contrario obligaria a reconstruir la posicion abierta en cada
    iteracion. Nace y muere dentro de una llamada, asi que dos corridas no
    pueden verse entre si.
    """

    engine: BacktestEngine
    compiled: CompiledStrategy
    decision: StrategyDecision
    bars: Bars
    atr: FloatArray
    instrument: Instrument
    initial_equity: float
    stop_multiple: float
    target_multiple: float

    position: Position | None = None
    cash: float = 0.0
    ambiguous: int = 0
    rejected: int = 0

    def execute(self) -> BacktestResult:
        n = len(self.bars)
        self.cash = self.initial_equity
        trades: list[Trade] = []
        equity = np.empty(n, dtype=np.float64)

        close = np.asarray(self.bars.close, dtype=np.float64)
        for index in range(n):
            self._open_of_bar(index, trades)
            self._during_bar(index, trades)
            equity[index] = self.cash + self._unrealized(close[index])

        self._close_at_end(n - 1, trades)
        if trades and trades[-1].exit_reason is ExitReason.END_OF_DATA:
            equity[n - 1] = self.cash

        return BacktestResult(
            trades=tuple(trades),
            equity=equity,
            initial_equity=self.initial_equity,
            ambiguous_bars=self.ambiguous,
            rejected_by_risk=self.rejected,
            warmup=self.decision.warmup,
        )

    # -- fases de una barra ---------------------------------------------------

    def _open_of_bar(self, index: int, trades: list[Trade]) -> None:
        """Ejecuta en la apertura de `i` lo que decidio el cierre de `i-1`.

        Salidas antes que entradas: una senal de reversion tiene que cerrar lo
        que hay antes de abrir lo contrario, y el orden inverso dejaria dos
        posiciones abiertas un instante que en vivo no existe.
        """
        if index == 0:
            return
        previous = index - 1
        price = float(np.asarray(self.bars.open)[index])

        if self.position is not None and self._exit_signalled(previous):
            self._close(index, price, ExitReason.SIGNAL_EXIT, trades)

        wanted = int(self.decision.direction[previous])
        if wanted == 0:
            return
        if self.position is not None:
            if int(self.position.direction) == wanted:
                return  # Ya posicionado en esa direccion: no se piramida.
            self._close(index, price, ExitReason.SIGNAL_REVERSE, trades)
        self._open(index, Direction(wanted))

    def _during_bar(self, index: int, trades: list[Trade]) -> None:
        """Vive la barra: excursiones primero, proteccion despues."""
        if self.position is None:
            return
        high = float(np.asarray(self.bars.high)[index])
        low = float(np.asarray(self.bars.low)[index])

        self.position = observe_bar(self.position, bar_high=high, bar_low=low)
        hit = resolve_bar_exit(self.position, bar_high=high, bar_low=low)
        if hit is None:
            return
        if hit.ambiguous:
            self.ambiguous += 1
        self._close(index, hit.price, hit.reason, trades)

    def _close_at_end(self, index: int, trades: list[Trade]) -> None:
        """Cierra lo que quede al agotarse la historia.

        Dejar una posicion abierta al final falsearia el resultado: su
        no realizado no es un beneficio y contarlo como tal premiaria a las
        estrategias que simplemente no cierran.
        """
        if self.position is None or index < 0:
            return
        self._close(
            index, float(np.asarray(self.bars.close)[index]), ExitReason.END_OF_DATA, trades
        )

    # -- operaciones ----------------------------------------------------------

    def _open(self, index: int, direction: Direction) -> None:
        stop_distance = float(self.atr[index - 1]) * self.stop_multiple
        if not np.isfinite(stop_distance) or stop_distance <= 0.0:
            self.rejected += 1
            return

        equity = self.cash + self._unrealized(float(np.asarray(self.bars.close)[index]))
        at_ns = TimestampNs(int(self.bars.timestamp[index]))
        intent, _ = self.engine.risk.size_order(
            intent_direction=int(direction),
            instrument=self.instrument,
            equity=equity,
            stop_distance=stop_distance,
            open_positions=(),
            at_ns=at_ns,
        )
        if intent is None:
            self.rejected += 1
            return

        fill = self._fill(intent, index)
        stop, target = protective_levels(
            direction=direction,
            entry_price=fill,
            stop_distance=stop_distance,
            instrument=self.instrument,
            target_distance=(
                float(self.atr[index - 1]) * self.target_multiple
                if self.target_multiple > 0.0
                else None
            ),
        )
        self.position = Position(
            symbol=self.instrument.symbol,
            direction=direction,
            lots=intent.lots,
            entry_price=fill,
            entry_time=at_ns,
            stop_loss=stop,
            take_profit=target,
            strategy_id=self.compiled.strategy_id,
        )

    def _close(self, index: int, price: float, reason: ExitReason, trades: list[Trade]) -> None:
        assert self.position is not None
        trade = close_position(
            self.position,
            exit_price=price,
            exit_time=TimestampNs(int(self.bars.timestamp[index])),
            reason=reason,
            instrument=self.instrument,
            atr=float(self.atr[index]) if np.isfinite(self.atr[index]) else 0.0,
        )
        trades.append(trade)
        self.cash += trade.net_pnl
        self.position = None

    # -- apoyo ----------------------------------------------------------------

    def _fill(self, intent: OrderIntent, index: int) -> float:
        arrays = (self.bars.open, self.bars.high, self.bars.low)
        opening, high, low = (float(np.asarray(a)[index]) for a in arrays)
        return float(
            self.engine.simulator.fill_price(
                intent=intent,
                instrument=self.instrument,
                bar_open=opening,
                bar_high=high,
                bar_low=low,
                atr=float(self.atr[index]) if np.isfinite(self.atr[index]) else 0.0,
            )
        )

    def _exit_signalled(self, index: int) -> bool:
        assert self.position is not None
        if self.position.direction is Direction.LONG:
            return bool(self.decision.exit_long[index])
        return bool(self.decision.exit_short[index])

    def _unrealized(self, price: float) -> float:
        if self.position is None:
            return 0.0
        return unrealized_pnl(self.position, price=price, instrument=self.instrument)


def as_port_result(result: BacktestResult) -> tuple[Sequence[Trade], FloatArray]:
    """Proyecta al par que declara `BacktestEnginePort.run`.

    El puerto promete `(trades, equity)` y el motor devuelve ademas el detalle
    que hace desconfiar del resultado -barras ambiguas, rechazos de riesgo-.
    Ampliar el puerto obligaria a todo consumidor a conocer ese detalle; esta
    proyeccion deja que quien solo quiera el par lo tenga.
    """
    return result.trades, result.equity


__all__ = [
    "DEFAULT_ATR_MULTIPLE",
    "DEFAULT_ATR_PERIOD",
    "DEFAULT_TARGET_MULTIPLE",
    "BacktestEngine",
    "BacktestResult",
    "as_port_result",
]
