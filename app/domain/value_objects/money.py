"""Objetos de valor monetarios y adimensionales.

`Money` y `Ratio` existen para que el sistema de tipos impida sumar euros con
porcentajes. Es la clase de error que no produce excepcion, produce un numero
plausible y equivocado.

Nota sobre precision: se usa `float64`, no `Decimal`. El motivo es que estas
cifras alimentan calculos vectorizados de numpy sobre cientos de miles de
barras, y convertir a `Decimal` en esa ruta multiplicaria el coste por dos
ordenes de magnitud. La contrapartida es que estos objetos NO sirven para
contabilidad ni conciliacion con el broker; para eso se usa el importe
reportado por el broker, que es la autoridad.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """Importe con divisa explicita.

    Las operaciones entre importes de divisas distintas fallan en lugar de
    convertir con una tasa implicita. Convertir requiere una tasa, la tasa
    requiere una fecha, y ninguna de las dos cosas pertenece a este objeto.
    """

    amount: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.currency or len(self.currency) > 8:
            raise InvariantViolation("Divisa invalida", currency=self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvariantViolation(
                "Operacion entre divisas distintas",
                left=self.currency,
                right=other.currency,
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: float) -> Money:
        return Money(self.amount * float(factor), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    @property
    def is_positive(self) -> bool:
        return self.amount > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency}

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"


@dataclass(frozen=True, slots=True, order=True)
class Ratio:
    """Magnitud adimensional expresada como fraccion, nunca como porcentaje.

    Toda la plataforma almacena fracciones (0.02) y solo formatea a porcentaje
    en la frontera de presentacion. Mezclar ambas convenciones dentro del
    sistema produce errores de factor 100 que pasan desapercibidos cuando el
    valor es pequeno.
    """

    value: float
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if self.lower is not None and self.value < self.lower:
            raise InvariantViolation(
                "Ratio por debajo de su cota", value=self.value, lower=self.lower
            )
        if self.upper is not None and self.value > self.upper:
            raise InvariantViolation(
                "Ratio por encima de su cota", value=self.value, upper=self.upper
            )

    @classmethod
    def fraction(cls, value: float) -> Ratio:
        """Ratio acotado a [0, 1]: proporciones, exposiciones, tasas de acierto."""
        return cls(value=value, lower=0.0, upper=1.0)

    @classmethod
    def percent(cls, percent_value: float) -> Ratio:
        """Constructor desde porcentaje humano. `Ratio.percent(2)` es 0.02."""
        return cls(value=percent_value / 100.0)

    @property
    def as_percent(self) -> float:
        return self.value * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value}

    def __str__(self) -> str:
        return f"{self.as_percent:.2f}%"


__all__ = ["Money", "Ratio"]
