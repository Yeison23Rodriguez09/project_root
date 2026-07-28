"""Intencion de orden, orden y su maquina de estados.

Se distinguen dos objetos que a menudo se confunden:

* `OrderIntent` es lo que la estrategia *quiere*: direccion, tamano y niveles.
  Es puro dominio y no conoce al broker.
* `Order` es lo que existe en el mundo: tiene identidad, estado y ecos del
  broker.

Esa separacion es la que permite que backtest, paper y live compartan la misma
logica de estrategia: los tres producen `OrderIntent` identicos y solo difieren
en como los convierten en `Order`. Es tambien la base de `execution_engine =
"event-driven"`: los eventos que se registran son transiciones de esta maquina.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final, Self

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, StrategyId, Symbol, TimestampNs


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderState(StrEnum):
    """Estados posibles de una orden.

    Las transiciones legales estan declaradas en `_TRANSITIONS`. Cualquier
    intento de transicion no declarada es un `InvariantViolation`: un estado
    imposible en la maquina de ordenes es la clase de bug que en vivo cuesta
    dinero, y debe fallar de inmediato en lugar de degradarse.
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


#: Transiciones legales. `Final` declara que es una constante y no estado
#: mutable de modulo: la tabla se lee, nunca se modifica en caliente. La regla
#: `forbid_global_mutable_state` de conventions.toml lo exige.
_TRANSITIONS: Final[dict[OrderState, frozenset[OrderState]]] = {
    OrderState.PENDING: frozenset(
        {OrderState.SUBMITTED, OrderState.CANCELLED, OrderState.REJECTED}
    ),
    OrderState.SUBMITTED: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED}
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED}
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}

#: Estados terminales: no admiten ninguna transicion posterior.
TERMINAL_STATES: frozenset[OrderState] = frozenset(
    state for state, targets in _TRANSITIONS.items() if not targets
)


def can_transition(source: OrderState, target: OrderState) -> bool:
    return target in _TRANSITIONS[source]


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Decision de trading expresada en terminos de dominio, sin broker.

    Attributes:
        symbol: Instrumento objetivo.
        direction: LONG o SHORT. `FLAT` no es una intencion valida; para cerrar
            se emite un `OrderIntent` con `reduce_only=True`.
        lots: Volumen ya ajustado a la rejilla del instrumento.
        order_type: Tipo de orden.
        limit_price: Precio para LIMIT o STOP; `None` en MARKET.
        stop_loss / take_profit: Niveles absolutos de precio, ya calculados por
            la capa de riesgo. El motor de ejecucion no los recalcula.
        decided_at: Timestamp de cierre de la barra que origino la decision.
            No es el momento de envio: esa distincion es la que hace auditable
            el retardo de ejecucion.
        strategy_id: Origen de la decision, para atribucion de resultados.
        tag: Etiqueta libre de trazabilidad (p.ej. nombre del bloque de salida).
    """

    symbol: Symbol
    direction: Direction
    lots: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    decided_at: TimestampNs | None = None
    strategy_id: StrategyId | None = None
    reduce_only: bool = False
    tag: str = ""

    def __post_init__(self) -> None:
        if self.direction == Direction.FLAT:
            raise InvariantViolation("OrderIntent no admite direccion FLAT")
        if self.lots <= 0:
            raise InvariantViolation("lots debe ser positivo", lots=self.lots)
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise InvariantViolation("Una orden MARKET no lleva limit_price")
        if self.order_type is not OrderType.MARKET and self.limit_price is None:
            raise InvariantViolation(f"{self.order_type} exige limit_price")
        self._validate_protective_levels()

    def _validate_protective_levels(self) -> None:
        """Comprueba que stop y objetivo estan del lado correcto.

        Un stop loss por encima del precio en una posicion larga es un error de
        signo que en backtest produce equity espectacular y en vivo produce
        cierre inmediato. Se detecta en construccion.

        Solo se puede comprobar cuando hay un precio de referencia, es decir en
        ordenes LIMIT y STOP. En MARKET la referencia es el precio de llenado,
        que aun no existe; la comprobacion equivalente la hace el motor de
        ejecucion al conocerlo.
        """
        reference = self.limit_price
        if reference is None or (self.stop_loss is None and self.take_profit is None):
            return
        is_long = self.direction == Direction.LONG
        if self.stop_loss is not None:
            wrong = self.stop_loss >= reference if is_long else self.stop_loss <= reference
            if wrong:
                raise InvariantViolation(
                    "stop_loss del lado incorrecto",
                    direction=self.direction.name,
                    stop_loss=self.stop_loss,
                    reference=reference,
                )
        if self.take_profit is not None:
            wrong = self.take_profit <= reference if is_long else self.take_profit >= reference
            if wrong:
                raise InvariantViolation(
                    "take_profit del lado incorrecto",
                    direction=self.direction.name,
                    take_profit=self.take_profit,
                    reference=reference,
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "direction": self.direction.name,
            "lots": self.lots,
            "order_type": str(self.order_type),
            "limit_price": self.limit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "decided_at": int(self.decided_at) if self.decided_at is not None else None,
            "strategy_id": str(self.strategy_id) if self.strategy_id else None,
            "reduce_only": self.reduce_only,
            "tag": self.tag,
        }


@dataclass(frozen=True, slots=True)
class Order:
    """Orden con identidad y estado observable.

    Es inmutable: cada transicion produce un objeto nuevo y acumula el estado
    en `history`. Asi el recorrido completo de una orden queda registrado por
    construccion, sin depender de que alguien se acordara de loguear cada paso.
    """

    order_id: str
    intent: OrderIntent
    state: OrderState = OrderState.PENDING
    filled_lots: float = 0.0
    average_price: float | None = None
    broker_ref: str | None = None
    submitted_at: TimestampNs | None = None
    updated_at: TimestampNs | None = None
    reject_reason: str | None = None
    history: tuple[OrderState, ...] = field(default=(OrderState.PENDING,))

    def transition(
        self,
        target: OrderState,
        *,
        at: TimestampNs | None = None,
        filled_lots: float | None = None,
        average_price: float | None = None,
        broker_ref: str | None = None,
        reject_reason: str | None = None,
    ) -> Self:
        """Devuelve una nueva orden en el estado destino.

        Raises:
            InvariantViolation: si la transicion no esta declarada como legal.
        """
        if not can_transition(self.state, target):
            raise InvariantViolation(
                "Transicion de orden ilegal",
                order_id=self.order_id,
                source=str(self.state),
                target=str(target),
                allowed=sorted(str(s) for s in _TRANSITIONS[self.state]),
            )
        return replace(
            self,
            state=target,
            updated_at=at if at is not None else self.updated_at,
            filled_lots=self.filled_lots if filled_lots is None else filled_lots,
            average_price=self.average_price if average_price is None else average_price,
            broker_ref=self.broker_ref if broker_ref is None else broker_ref,
            reject_reason=self.reject_reason if reject_reason is None else reject_reason,
            history=(*self.history, target),
        )

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_open(self) -> bool:
        return not self.is_terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "state": str(self.state),
            "filled_lots": self.filled_lots,
            "average_price": self.average_price,
            "broker_ref": self.broker_ref,
            "submitted_at": int(self.submitted_at) if self.submitted_at is not None else None,
            "updated_at": int(self.updated_at) if self.updated_at is not None else None,
            "reject_reason": self.reject_reason,
            "history": [str(s) for s in self.history],
            "intent": self.intent.to_dict(),
        }


__all__ = [
    "TERMINAL_STATES",
    "Order",
    "OrderIntent",
    "OrderState",
    "OrderType",
    "can_transition",
]
