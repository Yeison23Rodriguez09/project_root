"""Posiciones abiertas y operaciones cerradas.

`Trade` es la unidad de analisis de toda la plataforma: metricas, walk-forward,
validacion estadistica y atribucion parten de una lista de `Trade`. Por eso
guarda no solo el PnL sino el contexto que permite explicarlo (motivo de
salida, MAE/MFE, costes desglosados).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, StrategyId, Symbol, TimestampNs


class ExitReason(StrEnum):
    """Motivo por el que se cerro una operacion.

    Es un campo de primera clase, no un comentario: la distribucion de motivos
    de salida distingue una estrategia con gestion real de una que solo
    sobrevive por stops amplios. Si el 90% de las salidas son `STOP_LOSS`, la
    logica de salida no esta aportando nada.
    """

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    SIGNAL_REVERSE = "SIGNAL_REVERSE"
    TIME_EXIT = "TIME_EXIT"
    SESSION_CLOSE = "SESSION_CLOSE"
    RISK_STOP = "RISK_STOP"
    END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True, slots=True)
class Position:
    """Exposicion viva en un instrumento.

    Attributes:
        mae_price / mfe_price: Precio mas adverso y mas favorable alcanzados
            mientras la posicion estuvo abierta. Se actualizan barra a barra y
            alimentan el analisis de calidad de entrada: una estrategia con MFE
            alto y resultado bajo tiene un problema de salida, no de entrada.
    """

    symbol: Symbol
    direction: Direction
    lots: float
    entry_price: float
    entry_time: TimestampNs
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_id: StrategyId | None = None
    bars_held: int = 0
    mae_price: float | None = None
    mfe_price: float | None = None

    def __post_init__(self) -> None:
        if self.direction == Direction.FLAT:
            raise InvariantViolation("Una posicion no puede ser FLAT")
        if self.lots <= 0:
            raise InvariantViolation("lots debe ser positivo", lots=self.lots)

    def unrealized_points(self, price: float) -> float:
        """Resultado no realizado en unidades de precio, con signo."""
        return (price - self.entry_price) * int(self.direction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "direction": self.direction.name,
            "lots": self.lots,
            "entry_price": self.entry_price,
            "entry_time": int(self.entry_time),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy_id": str(self.strategy_id) if self.strategy_id else None,
            "bars_held": self.bars_held,
        }


@dataclass(frozen=True, slots=True)
class Trade:
    """Operacion cerrada, con costes desglosados.

    `gross_pnl` es el resultado teorico y `net_pnl` el resultado despues de
    costes. Mantener ambos es obligatorio: la diferencia entre los dos es la
    metrica que revela estrategias cuyo edge desaparece al pagar el mercado.
    """

    symbol: Symbol
    direction: Direction
    lots: float
    entry_time: TimestampNs
    entry_price: float
    exit_time: TimestampNs
    exit_price: float
    exit_reason: ExitReason
    gross_pnl: float
    commission: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    bars_held: int = 0
    mae: float = 0.0
    mfe: float = 0.0
    strategy_id: StrategyId | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        if self.exit_time < self.entry_time:
            raise InvariantViolation(
                "La salida precede a la entrada",
                entry_time=int(self.entry_time),
                exit_time=int(self.exit_time),
            )
        if self.lots <= 0:
            raise InvariantViolation("lots debe ser positivo", lots=self.lots)
        for name in ("commission", "spread_cost", "slippage_cost"):
            if getattr(self, name) < 0:
                raise InvariantViolation(f"{name} no puede ser negativo")

    @property
    def total_cost(self) -> float:
        return self.commission + self.spread_cost + self.slippage_cost

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.total_cost

    @property
    def is_winner(self) -> bool:
        """Se evalua sobre el resultado neto, nunca sobre el bruto."""
        return self.net_pnl > 0.0

    @property
    def duration_ns(self) -> int:
        return int(self.exit_time) - int(self.entry_time)

    @property
    def return_points(self) -> float:
        return (self.exit_price - self.entry_price) * int(self.direction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "direction": self.direction.name,
            "lots": self.lots,
            "entry_time": int(self.entry_time),
            "entry_price": self.entry_price,
            "exit_time": int(self.exit_time),
            "exit_price": self.exit_price,
            "exit_reason": str(self.exit_reason),
            "gross_pnl": self.gross_pnl,
            "commission": self.commission,
            "spread_cost": self.spread_cost,
            "slippage_cost": self.slippage_cost,
            "net_pnl": self.net_pnl,
            "bars_held": self.bars_held,
            "mae": self.mae,
            "mfe": self.mfe,
            "strategy_id": str(self.strategy_id) if self.strategy_id else None,
            "tag": self.tag,
        }


__all__ = ["ExitReason", "Position", "Trade"]
