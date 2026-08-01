"""Reglas de vida de una posicion. Las MISMAS en backtest y en vivo.

Viven aqui y no en `research/backtest` ni en `live` por una razon estructural:
`architecture.toml` deja `research`, `execution`, `portfolio` y `analytics` como
hermanos de rango 3, y ninguno ve a los otros. El unico sitio visible para todos
es `domain`. Que ademas sea el sitio correcto no es casualidad: donde se coloca
un stop y cuando se considera tocado son reglas de negocio, no detalles de
infraestructura.

Lo que difiere entre simular y operar de verdad queda fuera de aqui y se reduce
a tres cosas: de donde salen las barras, a que precio se llena una orden -eso lo
resuelve `ExecutionSimulatorPort`- y como se envia. Todo lo demas es este
modulo.

Funciones puras: reciben todo por argumento, no tocan reloj ni disco, y ninguna
devuelve el objeto que recibio modificado.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, TimestampNs
from app.domain.entities.trade import ExitReason, Position, Trade
from app.domain.value_objects.instrument import Instrument

#: Nanosegundos en un dia. La financiacion se cobra por dia mantenido.
_NANOS_PER_DAY = 86_400_000_000_000


def protective_levels(
    *,
    direction: Direction,
    entry_price: float,
    stop_distance: float,
    instrument: Instrument,
    target_distance: float | None = None,
) -> tuple[float, float | None]:
    """Niveles absolutos de proteccion a partir del precio de entrada.

    Es lo que `OrderIntent` deja pendiente para las ordenes a mercado: alli el
    precio de referencia es el de llenado, que no existe hasta que la orden se
    ejecuta. Esta funcion es la que lo cierra, y la usan igual el simulador y el
    ejecutor real.

    Los niveles se redondean a la precision cotizable del instrumento porque un
    stop con mas decimales de los que el broker admite es un stop que el broker
    reajusta por su cuenta, y entonces el nivel simulado y el real dejan de
    coincidir sin que nada lo avise.

    Raises:
        InvariantViolation: la distancia al stop no es positiva. Sin ella no hay
            proteccion, y aceptarla en silencio dejaria la posicion desnuda.
    """
    if stop_distance <= 0.0:
        raise InvariantViolation(
            "La distancia al stop debe ser positiva", stop_distance=stop_distance
        )
    if direction is Direction.FLAT:
        raise InvariantViolation("Una posicion plana no tiene niveles que proteger")

    sign = int(direction)
    stop = instrument.round_price(entry_price - sign * stop_distance)
    if target_distance is None:
        return stop, None
    if target_distance <= 0.0:
        raise InvariantViolation(
            "La distancia al objetivo debe ser positiva", target_distance=target_distance
        )
    return stop, instrument.round_price(entry_price + sign * target_distance)


@dataclass(frozen=True, slots=True)
class BarExit:
    """Salida provocada por el recorrido de una barra, con su honestidad medida.

    Attributes:
        reason: `STOP_LOSS` o `TAKE_PROFIT`.
        price: Nivel al que se asume el llenado. Es el nivel declarado y no un
            precio del mercado: un stop se ejecuta EN el stop salvo hueco.
        ambiguous: La barra contenia los dos niveles. Ver `resolve_bar_exit`.
    """

    reason: ExitReason
    price: float
    ambiguous: bool = False


def resolve_bar_exit(position: Position, *, bar_high: float, bar_low: float) -> BarExit | None:
    """Decide si el recorrido de una barra cerro la posicion, y por que.

    AQUI ESTA LA MENTIRA MAS COMUN DE CUALQUIER BACKTEST. Cuando el stop y el
    objetivo caen los dos dentro del rango de la misma vela, el OHLC no contiene
    la informacion necesaria para saber cual se toco primero: haria falta el
    tick. Suponer el objetivo produce curvas de equity espectaculares y falsas,
    y no falla en ninguna parte porque el dato que las desmentiria no existe.

    Se resuelve SIEMPRE a favor del stop, y se marca la barra como ambigua. Lo
    primero es la cota pesimista -si la estrategia sigue valiendo con ella, vale
    de verdad-. Lo segundo importa igual: una estrategia con muchas barras
    ambiguas depende de una suposicion que ningun dato sostiene, y sin contarlas
    esa fragilidad no aparece en ninguna metrica.
    """
    if position.stop_loss is None and position.take_profit is None:
        return None

    long = position.direction is Direction.LONG
    stop_hit = position.stop_loss is not None and (
        bar_low <= position.stop_loss if long else bar_high >= position.stop_loss
    )
    target_hit = position.take_profit is not None and (
        bar_high >= position.take_profit if long else bar_low <= position.take_profit
    )

    if stop_hit and target_hit:
        assert position.stop_loss is not None
        return BarExit(ExitReason.STOP_LOSS, position.stop_loss, ambiguous=True)
    if stop_hit:
        assert position.stop_loss is not None
        return BarExit(ExitReason.STOP_LOSS, position.stop_loss)
    if target_hit:
        assert position.take_profit is not None
        return BarExit(ExitReason.TAKE_PROFIT, position.take_profit)
    return None


def observe_bar(position: Position, *, bar_high: float, bar_low: float) -> Position:
    """Actualiza excursiones y barras mantenidas tras vivir una barra.

    Devuelve una posicion NUEVA: la original no se toca, de modo que un motor
    que conserve la anterior por cualquier motivo no vea cambiar el pasado.

    MAE y MFE se miden sobre el recorrido intrabarra y no sobre los cierres. Una
    posicion que aguanto un retroceso del 80% del stop y acabo ganando es una
    posicion afortunada, y medirla solo por cierres la haria parecer comoda.
    """
    adverse, favourable = (
        (bar_low, bar_high)
        if position.direction is Direction.LONG
        else (
            bar_high,
            bar_low,
        )
    )
    worst = adverse if position.mae_price is None else _worse(position, adverse)
    best = favourable if position.mfe_price is None else _better(position, favourable)

    return replace(
        position,
        bars_held=position.bars_held + 1,
        mae_price=worst,
        mfe_price=best,
    )


def _worse(position: Position, candidate: float) -> float:
    assert position.mae_price is not None
    if position.direction is Direction.LONG:
        return min(position.mae_price, candidate)
    return max(position.mae_price, candidate)


def _better(position: Position, candidate: float) -> float:
    assert position.mfe_price is not None
    if position.direction is Direction.LONG:
        return max(position.mfe_price, candidate)
    return min(position.mfe_price, candidate)


def trade_costs(
    *, position: Position, exit_time: TimestampNs, instrument: Instrument, atr: float = 0.0
) -> tuple[float, float, float, float]:
    """Comision, spread, deslizamiento y financiacion de una operacion cerrada.

    Se devuelven desglosados y no sumados porque `Trade` los guarda por separado
    a proposito: la diferencia entre bruto y neto es lo que revela una estrategia
    cuyo edge desaparece al pagar el mercado, y para saber CUAL coste se la come
    hay que poder mirarlos uno a uno.

    Comision y deslizamiento se cobran DOS veces -entrada y salida- y el spread
    una sola: se paga al cruzar, no al deshacer. La financiacion escala con los
    dias completos mantenidos.
    """
    costs = instrument.costs
    lots = position.lots
    per_point = instrument.value_per_point_per_lot

    commission = costs.commission_per_lot * lots * 2.0
    spread = costs.spread_points * per_point * lots
    slip_points = costs.slippage_points + costs.slippage_atr_multiple * instrument.price_to_points(
        atr
    )
    slippage = slip_points * per_point * lots * 2.0

    days = max(0, int(exit_time) - int(position.entry_time)) / _NANOS_PER_DAY
    financing = costs.financing_per_lot_per_day * lots * days

    return commission, spread, slippage, financing


def close_position(
    position: Position,
    *,
    exit_price: float,
    exit_time: TimestampNs,
    reason: ExitReason,
    instrument: Instrument,
    atr: float = 0.0,
) -> Trade:
    """Convierte una posicion viva en una operacion cerrada con costes.

    `gross_pnl` sale de los precios y nada mas: es el resultado teorico. Los
    costes viajan aparte para que `net_pnl` los reste y la diferencia entre los
    dos siga siendo medible.
    """
    move = (exit_price - position.entry_price) * int(position.direction)
    gross = instrument.money_from_price_move(move, position.lots)
    commission, spread, slippage, financing = trade_costs(
        position=position, exit_time=exit_time, instrument=instrument, atr=atr
    )

    return Trade(
        symbol=position.symbol,
        direction=position.direction,
        lots=position.lots,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=reason,
        gross_pnl=gross,
        commission=commission,
        spread_cost=spread,
        slippage_cost=slippage,
        financing_cost=financing,
        bars_held=position.bars_held,
        mae=_excursion(position, position.mae_price, instrument),
        mfe=_excursion(position, position.mfe_price, instrument),
        strategy_id=position.strategy_id,
    )


def _excursion(position: Position, price: float | None, instrument: Instrument) -> float:
    """Excursion en MONEDA DE CUENTA, con signo respecto a la entrada.

    En moneda y no en puntos porque `mae` y `mfe` viven en `Trade` junto a
    `gross_pnl`: mezclar unidades en el mismo objeto invita a compararlos sin
    convertir, y en un instrumento con `value_per_point_per_lot` distinto de uno
    esa comparacion es sencillamente falsa.
    """
    if price is None:
        return 0.0
    return instrument.money_from_price_move(
        (price - position.entry_price) * int(position.direction), position.lots
    )


def unrealized_pnl(position: Position, *, price: float, instrument: Instrument) -> float:
    """Resultado no realizado a un precio dado, en moneda de cuenta.

    Es lo que la curva de equity necesita en cada barra: sin marcar a mercado, el
    drawdown solo aparece cuando una operacion se cierra, y una estrategia que
    aguanta perdidas enormes sin cerrar pareceria comoda hasta el final.
    """
    return instrument.money_from_price_move(position.unrealized_points(price), position.lots)


__all__ = [
    "BarExit",
    "close_position",
    "observe_bar",
    "protective_levels",
    "resolve_bar_exit",
    "trade_costs",
    "unrealized_pnl",
]
