"""Dimensionamiento fraccional fijo: el unico lugar que decide cuanto se opera.

    presupuesto = capital * fraccion
    lotes       = presupuesto / (distancia_al_stop * valor_por_punto)

Es una DIVISION, y ahi esta todo el peligro de este motor: cuando la distancia
al stop tiende a cero el tamano tiende a infinito. Un stop de un punto pide cien
veces mas lotes que uno de cien, y lo pide justo en la operacion peor planteada.
Por eso el primer control no es el margen ni el numero de posiciones, sino que
el stop exista y sea mayor que el coste de operar.

Lo que este motor NO hace:

    no decide direccion     la trae la estrategia
    no decide entrada       no mira precio ni senal
    no calcula el stop      recibe la distancia, no la inventa
    no envia nada           produce una intencion; ejecutar es de otro

Y no conoce la evidencia estadistica. `architecture.toml` deja `portfolio` con
`depends = ["core", "domain", "shared"]`: ni walkforward ni validation. La
direccion es la correcta -el tamano es funcion del instrumento, el capital y la
distancia al stop, no de un p-valor-. Validation precede a Risk en el ciclo de
vida, no en el flujo de datos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, Severity, StrategyId, TimestampNs
from app.core.validation import ValidationReport
from app.domain.entities.order import OrderIntent, OrderType
from app.domain.entities.trade import Position
from app.domain.value_objects.instrument import Instrument
from app.portfolio.limits import RiskLimits


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """Como se llego al tamano, con el detalle que lo hace auditable.

    `raw_lots` se conserva junto a `lots` a proposito: sin el no se puede saber
    si el tamano final salio del presupuesto de riesgo o de un tope, y esas dos
    situaciones significan cosas distintas. Un tope que se activa a menudo es una
    politica mal calibrada; uno que no se activa nunca es un cortafuegos sano.

    Attributes:
        lots: Volumen final, ya ajustado a la rejilla del broker. `0.0` cuando
            no hay tamano seguro.
        raw_lots: Volumen que pedia el presupuesto, antes de topes y rejilla.
        risk_money: Capital arriesgado hasta el stop, en moneda de cuenta.
        stop_points: Distancia al stop en puntos del instrumento.
        binding: Limites que recortaron o bloquearon, en orden de aplicacion.
    """

    lots: float
    raw_lots: float
    risk_money: float
    stop_points: float
    margin_used: float
    binding: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tradable(self) -> bool:
        return self.lots > 0.0

    @property
    def reduced(self) -> bool:
        """Un tope recorto el tamano, pero la operacion sigue en pie."""
        return self.tradable and self.lots < self.raw_lots

    def to_dict(self) -> dict[str, Any]:
        return {
            "lots": self.lots,
            "raw_lots": self.raw_lots,
            "risk_money": self.risk_money,
            "stop_points": self.stop_points,
            "margin_used": self.margin_used,
            "binding": list(self.binding),
            "tradable": self.tradable,
            "reduced": self.reduced,
        }


class FixedFractionalRiskPolicy:
    """Arriesga una fraccion fija del capital actual hasta el stop.

    Cumple `shared.ports.RiskPolicyPort`. Es pura y determinista: mismos
    argumentos, mismo tamano, siempre. No consulta reloj -`at_ns` entra
    inyectado- ni fuente de datos ni broker.
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits if limits is not None else RiskLimits()

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def size_order(
        self,
        *,
        intent_direction: int,
        instrument: Instrument,
        equity: float,
        stop_distance: float,
        open_positions: Sequence[Position],
        at_ns: TimestampNs,
        strategy_id: StrategyId | None = None,
    ) -> tuple[OrderIntent | None, ValidationReport]:
        """Traduce una direccion en una intencion dimensionada, o en un rechazo.

        Devuelve `None` cuando no hay tamano seguro; el motivo viaja siempre en
        el informe, porque un rechazo sin motivo registrado obliga a repetir el
        trabajo para entenderlo.

        La intencion sale con `stop_loss=None` y no es un olvido: en una orden a
        mercado el precio de referencia es el de llenado, que todavia no existe.
        `OrderIntent` documenta que la comprobacion equivalente la hace el motor
        de ejecucion al conocerlo.
        """
        report = ValidationReport(subject=str(instrument.symbol))
        decision = self.decide(
            intent_direction=intent_direction,
            instrument=instrument,
            equity=equity,
            stop_distance=stop_distance,
            open_positions=open_positions,
            report=report,
        )
        if not decision.tradable:
            return None, report

        return (
            OrderIntent(
                symbol=instrument.symbol,
                direction=Direction(intent_direction),
                lots=decision.lots,
                order_type=OrderType.MARKET,
                decided_at=at_ns,
                strategy_id=strategy_id,
                tag="fixed_fractional",
            ),
            report,
        )

    def decide(
        self,
        *,
        intent_direction: int,
        instrument: Instrument,
        equity: float,
        stop_distance: float,
        open_positions: Sequence[Position],
        report: ValidationReport,
    ) -> SizingDecision:
        """Calcula el tamano y anota en `report` cada limite que intervino."""
        if not self._passes_gates(
            intent_direction, instrument, equity, stop_distance, open_positions, report
        ):
            return SizingDecision(
                lots=0.0, raw_lots=0.0, risk_money=0.0, stop_points=0.0, margin_used=0.0
            )

        stop_points = instrument.price_to_points(stop_distance)
        risk_money = equity * self._limits.risk_fraction

        # La division no lleva guarda porque el divisor es positivo por
        # construccion, no por confianza: `Instrument` rechaza en `__post_init__`
        # un `value_per_point_per_lot` no positivo y un `point` no positivo, y la
        # puerta de arriba ya exigio `stop_distance > 0`. Anadir aqui un `if`
        # sugeriria que esas invariantes no se sostienen, y ademas seria una rama
        # que ningun test podria alcanzar.
        money_per_lot = stop_points * instrument.value_per_point_per_lot
        raw_lots = risk_money / money_per_lot
        capped, binding = self._apply_caps(raw_lots, instrument, equity, open_positions, report)

        # Se redondea UNA vez, al final. Truncar tras cada tope acumularia
        # perdidas de rejilla y haria que el orden de aplicacion cambiase el
        # resultado; todos los topes son cotas superiores, asi que tomar el
        # minimo y truncar despues es equivalente y no depende del orden.
        lots = instrument.round_lots(capped)
        if lots <= 0.0:
            report.add(
                "BELOW_MINIMUM_LOT",
                f"El tamano admisible ({capped:.6f}) no alcanza el lote minimo "
                f"({instrument.min_lot}). Redondear al alza arriesgaria mas de lo "
                f"presupuestado, asi que no se opera.",
                Severity.ERROR,
                admissible=capped,
                min_lot=instrument.min_lot,
                raw_lots=raw_lots,
            )

        return SizingDecision(
            lots=lots,
            raw_lots=raw_lots,
            risk_money=risk_money,
            stop_points=stop_points,
            margin_used=lots * instrument.margin_per_lot,
            binding=binding,
        )

    # -- puertas: rechazan ---------------------------------------------------

    def _passes_gates(
        self,
        direction: int,
        instrument: Instrument,
        equity: float,
        stop_distance: float,
        open_positions: Sequence[Position],
        report: ValidationReport,
    ) -> bool:
        """Condiciones sin las cuales no existe un tamano seguro."""
        if direction not in (int(Direction.LONG), int(Direction.SHORT)):
            report.add(
                "INVALID_DIRECTION",
                "Solo LONG o SHORT producen una intencion",
                Severity.ERROR,
                direction=direction,
            )
        if equity <= 0.0:
            report.add(
                "NON_POSITIVE_EQUITY",
                "Sin capital no hay fraccion que arriesgar",
                Severity.ERROR,
                equity=equity,
            )
        self._check_stop(instrument, stop_distance, report)
        if len(open_positions) >= self._limits.max_open_positions:
            report.add(
                "MAX_POSITIONS_REACHED",
                f"Ya hay {len(open_positions)} posiciones abiertas y el limite "
                f"es {self._limits.max_open_positions}",
                Severity.ERROR,
                open_positions=len(open_positions),
                limit=self._limits.max_open_positions,
            )
        return report.ok

    def _check_stop(
        self, instrument: Instrument, stop_distance: float, report: ValidationReport
    ) -> None:
        """La distancia al stop: el divisor del tamano y el control que mas pesa.

        Se separa de las demas puertas porque es la unica que necesita conocer el
        instrumento y su modelo de coste, y porque agrupa tres comprobaciones
        distintas sobre el mismo dato.
        """
        if stop_distance <= 0.0:
            # El caso mas peligroso del motor: el tamano es el presupuesto
            # dividido por esta distancia.
            report.add(
                "NON_POSITIVE_STOP",
                "La distancia al stop debe ser positiva: es el divisor del tamano",
                Severity.ERROR,
                stop_distance=stop_distance,
            )
            return

        minimum = self._limits.min_stop_points_for(instrument)
        points = instrument.price_to_points(stop_distance)

        if instrument.costs.spread_points + instrument.costs.slippage_points <= 0.0:
            # `CostModel` vale cero por defecto, asi que un instrumento sin
            # calibrar es indistinguible de uno genuinamente barato. Cuando eso
            # pasa, el stop minimo cae al suelo absoluto y el limite mas
            # importante del motor queda mucho mas debil de lo que su autor
            # creia, sin que nada lo delate. Se anota para que la degradacion
            # aparezca en la auditoria en vez de ocurrir en silencio.
            report.add(
                "UNCALIBRATED_COST_MODEL",
                f"{instrument.symbol} declara coste cero, asi que el stop minimo "
                f"cae al suelo absoluto ({minimum:.1f} puntos). Con el coste real "
                f"del broker el minimo seria mayor y esta orden podria no ser "
                f"admisible.",
                Severity.WARNING,
                symbol=str(instrument.symbol),
                floor_points=minimum,
            )

        if points < minimum:
            report.add(
                "STOP_TOO_TIGHT",
                f"El stop esta a {points:.1f} puntos y el minimo para este "
                f"instrumento es {minimum:.1f}. Un stop mas estrecho que el coste "
                f"de operar lo barre el ruido, y ademas pide el tamano mas grande "
                f"de toda la serie.",
                Severity.ERROR,
                stop_points=points,
                minimum_points=minimum,
            )

    # -- topes: reducen ------------------------------------------------------

    def _apply_caps(
        self,
        raw_lots: float,
        instrument: Instrument,
        equity: float,
        open_positions: Sequence[Position],
        report: ValidationReport,
    ) -> tuple[float, tuple[str, ...]]:
        """Recorta el tamano a la menor de las cotas. Nunca lo aumenta."""
        lots = raw_lots
        binding: list[str] = []

        for code, ceiling, context in self._ceilings(instrument, equity, open_positions):
            if ceiling < lots:
                report.add(
                    code,
                    f"Tope activo: {lots:.4f} lotes recortados a {max(ceiling, 0.0):.4f}",
                    Severity.WARNING,
                    requested=lots,
                    ceiling=ceiling,
                    **context,
                )
                binding.append(code)
                lots = max(ceiling, 0.0)

        return lots, tuple(binding)

    def _ceilings(
        self, instrument: Instrument, equity: float, open_positions: Sequence[Position]
    ) -> tuple[tuple[str, float, dict[str, Any]], ...]:
        """Cotas superiores al tamano, cada una con su contexto para el informe."""
        already = sum(p.lots for p in open_positions if p.symbol == instrument.symbol)
        ceilings: list[tuple[str, float, dict[str, Any]]] = [
            (
                "CAPPED_BY_MAX_LOTS",
                self._limits.max_lots_per_order,
                {"limit": self._limits.max_lots_per_order},
            ),
            (
                "CAPPED_BY_SYMBOL_EXPOSURE",
                self._limits.max_lots_per_symbol - already,
                {"limit": self._limits.max_lots_per_symbol, "already_open": already},
            ),
        ]
        if instrument.margin_per_lot > 0.0:
            budget = equity * self._limits.max_margin_utilization
            ceilings.append(
                (
                    "CAPPED_BY_MARGIN",
                    budget / instrument.margin_per_lot,
                    {"margin_budget": budget, "margin_per_lot": instrument.margin_per_lot},
                )
            )
        return tuple(ceilings)


def limits_from_mapping(raw: dict[str, Any]) -> RiskLimits:
    """Construye limites desde el mapa cargado de `configs/risk.toml`.

    Vive aqui y no en `limits.py` para que el objeto de valor no dependa de la
    forma del fichero: cambiar la disposicion del TOML no debe tocar el tipo.

    Raises:
        InvariantViolation: falta una seccion obligatoria.
    """
    try:
        sizing = raw["sizing"]
        limits = raw["limits"]
    except KeyError as error:
        raise InvariantViolation(
            "La configuracion de riesgo necesita [sizing] y [limits]", missing=str(error)
        ) from error

    return RiskLimits(
        risk_fraction=float(sizing["risk_fraction"]),
        min_stop_cost_multiple=float(limits["min_stop_cost_multiple"]),
        absolute_min_stop_points=float(limits["absolute_min_stop_points"]),
        max_lots_per_order=float(limits["max_lots_per_order"]),
        max_lots_per_symbol=float(limits["max_lots_per_symbol"]),
        max_open_positions=int(limits["max_open_positions"]),
        max_margin_utilization=float(limits["max_margin_utilization"]),
    )


__all__ = [
    "FixedFractionalRiskPolicy",
    "SizingDecision",
    "limits_from_mapping",
]
