"""Modelos de llenado. La UNICA diferencia real entre simular y operar.

`ExecutionSimulatorPort` existe, segun su propio contrato, "para poder sustituir
el modelo optimista por uno pesimista y comparar: la diferencia entre ambos es
una medida directa de la fragilidad de la estrategia frente a la calidad de
ejecucion". Este modulo aporta los dos extremos de esa comparacion.

Que devuelven: el precio de REFERENCIA al que la orden transacciona, sin costes.
El spread, la comision, el deslizamiento y la financiacion no se meten aqui: los
lleva `Trade` desglosados, porque `gross_pnl` esta definido como "el resultado
teorico" y `net_pnl` como el resultado tras pagarlos. Si el precio de llenado ya
trajera los costes dentro, bruto y neto serian el mismo numero y la metrica que
revela una estrategia que no sobrevive a los costes desapareceria.

Lo que si modelan es DONDE dentro de la barra ocurre el llenado, que es una
suposicion de verdad y no una tarifa.
"""

from __future__ import annotations

from app.core.exceptions import InvariantViolation
from app.core.types import Direction
from app.domain.entities.order import OrderIntent, OrderType
from app.domain.value_objects.instrument import Instrument


class OpenFillSimulator:
    """Llenado en la apertura de la barra. Es la referencia realista.

    La decision se toma al CIERRE de la barra `i` y la orden se envia a mercado,
    de modo que se llena en la apertura de `i+1` (`behavior.causal_features_only`
    de `configs/architecture.toml`, P1). Este modelo asume
    que esa apertura es alcanzable, que es cierto salvo en huecos, y que el hueco
    ya viene dentro del precio de apertura.
    """

    @property
    def name(self) -> str:
        return "OPEN"

    def fill_price(
        self,
        *,
        intent: OrderIntent,
        instrument: Instrument,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        atr: float,
    ) -> float:
        del atr
        _reject_flat(intent)
        if intent.order_type is OrderType.MARKET:
            return instrument.round_price(bar_open)
        return _pending_fill(intent, instrument, bar_open, bar_high, bar_low)


class AdverseFillSimulator:
    """Llenado en el peor precio de la barra. Cota superior del dano.

    No pretende ser realista: pretende ser una cota. Si una estrategia sigue
    siendo rentable suponiendo que CADA orden se llena en el peor punto de su
    barra, su resultado no depende de la calidad de ejecucion. Si deja de serlo,
    la diferencia contra `OpenFillSimulator` mide exactamente cuanto de su edge
    vive de suponer llenados favorables.
    """

    @property
    def name(self) -> str:
        return "ADVERSE"

    def fill_price(
        self,
        *,
        intent: OrderIntent,
        instrument: Instrument,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        atr: float,
    ) -> float:
        del atr, bar_open
        _reject_flat(intent)
        worst = bar_high if intent.direction is Direction.LONG else bar_low
        return instrument.round_price(worst)


def _reject_flat(intent: OrderIntent) -> None:
    if intent.direction is Direction.FLAT:
        # `OrderIntent` ya lo prohibe en construccion; se comprueba igual porque
        # este puerto admite implementaciones de terceros que no pasan por el.
        raise InvariantViolation("Una intencion plana no se puede llenar")


def _pending_fill(
    intent: OrderIntent,
    instrument: Instrument,
    bar_open: float,
    bar_high: float,
    bar_low: float,
) -> float:
    """Llenado de una orden con precio declarado.

    Una orden pendiente solo se llena si la barra alcanza su precio, y cuando lo
    hace se llena EN el precio, no mejor: suponer mejora sistematica es la misma
    clase de optimismo que resolver a favor del objetivo una barra ambigua.

    La apertura manda sobre el nivel cuando ya lo ha superado: si la barra abre
    al otro lado del precio declarado, el llenado real ocurre en la apertura y no
    en el nivel, porque el mercado nunca estuvo ahi.
    """
    level = intent.limit_price
    if level is None:
        raise InvariantViolation(
            "Una orden no de mercado exige precio", order_type=str(intent.order_type)
        )

    # El hueco se resuelve ANTES de comprobar que la barra contiene el nivel, y
    # el orden importa: un hueco es precisamente una barra que NO contiene el
    # nivel porque abrio mas alla. Comprobar la contencion primero rechazaria el
    # unico caso que esta rama existe para tratar, y la dejaria inalcanzable.
    gapped = bar_open > level if intent.direction is Direction.LONG else bar_open < level
    if intent.order_type is OrderType.STOP and gapped:
        # Un stop de compra con hueco al alza se llena peor que su nivel: el
        # mercado nunca estuvo ahi, y suponer que si lo estuvo es la misma clase
        # de optimismo que resolver a favor del objetivo una barra ambigua.
        return instrument.round_price(bar_open)

    if not (bar_low <= level <= bar_high):
        raise InvariantViolation(
            "La barra no alcanza el precio declarado",
            level=level,
            bar_low=bar_low,
            bar_high=bar_high,
        )

    # Una LIMIT que abre mejor que su precio SI podria llenarse mejor, y aun asi
    # se llena en el nivel: la mejora depende de la profundidad del libro, que
    # una barra OHLC no contiene. Suponerla seria inventar rentabilidad.
    return instrument.round_price(level)


__all__ = ["AdverseFillSimulator", "OpenFillSimulator"]
