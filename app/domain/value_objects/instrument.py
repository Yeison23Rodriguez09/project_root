"""Especificacion de un instrumento negociable.

Separar el instrumento de la estrategia es lo que permite que un mismo motor
opere EURUSD y XAUUSD sin ramas condicionales por simbolo. Todo lo que depende
del mercado concreto (tamano de tick, valor del punto, costes, limites) vive
aqui y llega al motor como dato, no como codigo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import Symbol


@dataclass(frozen=True, slots=True)
class CostModel:
    """Modelo de coste de ejecucion.

    Los tres componentes se modelan por separado porque se comportan de forma
    distinta: la comision escala con el volumen, el spread con la liquidez y el
    slippage con la volatilidad y el tipo de orden. Colapsarlos en un unico
    numero impide diagnosticar donde se pierde el edge.

    Attributes:
        commission_per_lot: Coste fijo por lote y por lado, en moneda de cuenta.
        spread_points: Spread tipico en puntos del instrumento. Se aplica como
            coste de cruce al entrar y al salir.
        slippage_points: Deslizamiento fijo asumido por orden a mercado.
        slippage_atr_multiple: Componente de slippage proporcional al ATR
            vigente. Modela que en regimenes volatiles el deslizamiento crece.
            Cero desactiva el componente.
        financing_per_lot_per_day: Coste de mantener un lote abierto un dia, en
            moneda de cuenta. **Puede ser negativo**: el carry a favor existe y
            un modelo que lo prohiba solo puede representar el lado caro.

            Es el cuarto componente y llego en ADR-0010. Los otros tres son
            costes de CRUCE -se pagan al entrar y al salir-; este es coste de
            TIEMPO, y por eso ninguno de ellos podia representarlo. Sin el,
            `Trade.net_pnl` era optimista para toda estrategia mantenida entre
            sesiones, de forma sistematica y silenciosa.

            La forma del parametro -por lote y dia frente a puntos por noche,
            y su asimetria entre largo y corto- se refinara cuando el adaptador
            MT5 lea las condiciones reales del terminal. Se declara ahora con la
            forma mas simple que representa el hecho, no con la definitiva.
    """

    commission_per_lot: float = 0.0
    spread_points: float = 0.0
    slippage_points: float = 0.0
    slippage_atr_multiple: float = 0.0
    financing_per_lot_per_day: float = 0.0

    def __post_init__(self) -> None:
        # `financing_per_lot_per_day` queda fuera a proposito: es el unico coste
        # con signo. Incluirlo aqui prohibiria representar un carry favorable.
        for name in (
            "commission_per_lot",
            "spread_points",
            "slippage_points",
            "slippage_atr_multiple",
        ):
            value = getattr(self, name)
            if value < 0:
                raise InvariantViolation(f"{name} no puede ser negativo", value=value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commission_per_lot": self.commission_per_lot,
            "spread_points": self.spread_points,
            "slippage_points": self.slippage_points,
            "slippage_atr_multiple": self.slippage_atr_multiple,
            "financing_per_lot_per_day": self.financing_per_lot_per_day,
        }


@dataclass(frozen=True, slots=True)
class Instrument:
    """Contrato negociable con sus reglas de mercado.

    Attributes:
        symbol: Identificador estable del instrumento.
        point: Incremento minimo de cotizacion (p.ej. 0.00001 en EURUSD de 5
            decimales). Es la unidad en la que se expresan spread y slippage.
        digits: Decimales de cotizacion. Se usa para redondear precios de orden.
        contract_size: Unidades del activo por lote.
        value_per_point_per_lot: Valor monetario de un punto por lote, en moneda
            de cuenta. Se declara explicitamente en lugar de derivarlo, porque
            derivarlo exige el tipo de cambio de la divisa de cotizacion y ese
            calculo no pertenece al dominio.
        min_lot / max_lot / lot_step: Restricciones de volumen del broker.
        margin_per_lot: Margen requerido por lote, en moneda de cuenta.
        costs: Modelo de coste asociado.
        quote_currency: Moneda de cotizacion.
    """

    symbol: Symbol
    point: float
    digits: int
    contract_size: float
    value_per_point_per_lot: float
    min_lot: float
    max_lot: float
    lot_step: float
    margin_per_lot: float
    costs: CostModel
    quote_currency: str = "USD"

    def __post_init__(self) -> None:
        if self.point <= 0:
            raise InvariantViolation("point debe ser positivo", point=self.point)
        if self.digits < 0:
            raise InvariantViolation("digits no puede ser negativo", digits=self.digits)
        if self.contract_size <= 0:
            raise InvariantViolation("contract_size debe ser positivo")
        if self.value_per_point_per_lot <= 0:
            raise InvariantViolation("value_per_point_per_lot debe ser positivo")
        if not (0 < self.min_lot <= self.max_lot):
            raise InvariantViolation(
                "Rango de lotes invalido", min_lot=self.min_lot, max_lot=self.max_lot
            )
        if self.lot_step <= 0:
            raise InvariantViolation("lot_step debe ser positivo", lot_step=self.lot_step)
        if self.margin_per_lot < 0:
            raise InvariantViolation("margin_per_lot no puede ser negativo")

    # -- conversiones -------------------------------------------------------

    def points_to_price(self, points: float) -> float:
        """Convierte una distancia en puntos a una distancia en precio."""
        return points * self.point

    def price_to_points(self, price_delta: float) -> float:
        """Convierte una distancia en precio a puntos del instrumento."""
        return price_delta / self.point

    def money_from_price_move(self, price_delta: float, lots: float) -> float:
        """Resultado monetario de un movimiento de precio dado un volumen."""
        return self.price_to_points(price_delta) * self.value_per_point_per_lot * lots

    def round_price(self, price: float) -> float:
        """Ajusta un precio a la precision cotizable del instrumento."""
        return round(price, self.digits)

    def round_lots(self, lots: float) -> float:
        """Ajusta un volumen a la rejilla del broker, truncando hacia abajo.

        Se trunca y no se redondea: redondear al alza puede superar un limite
        de riesgo ya calculado. Un tamano ligeramente menor nunca rompe un
        limite; uno mayor si. Devuelve 0.0 si el resultado no alcanza el minimo
        negociable, para que el llamante trate explicitamente el caso "no se
        puede operar con este riesgo".
        """
        if lots <= 0:
            return 0.0
        steps = int(lots / self.lot_step + 1e-9)
        clipped = max(0.0, steps * self.lot_step)
        if clipped < self.min_lot:
            return 0.0
        return min(round(clipped, 8), self.max_lot)

    def is_tradable_volume(self, lots: float) -> bool:
        return self.min_lot <= lots <= self.max_lot

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "point": self.point,
            "digits": self.digits,
            "contract_size": self.contract_size,
            "value_per_point_per_lot": self.value_per_point_per_lot,
            "min_lot": self.min_lot,
            "max_lot": self.max_lot,
            "lot_step": self.lot_step,
            "margin_per_lot": self.margin_per_lot,
            "quote_currency": self.quote_currency,
            "costs": self.costs.to_dict(),
        }


__all__ = ["CostModel", "Instrument"]
