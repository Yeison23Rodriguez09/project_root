"""Declaracion del espacio de parametros de un componente.

`ParamSpec` es lo que convierte un catalogo en un espacio de busqueda. Sin el,
discovery necesitaria una lista de combinaciones mantenida a mano en paralelo
al codigo, y esa lista se desincronizaria en la primera semana.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.registry.exceptions import InvalidParameter


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """Dominio admisible de un parametro.

    Attributes:
        name: Nombre del argumento en la funcion del componente.
        default: Valor usado si la configuracion no lo especifica.
        choices: Conjunto discreto de valores que discovery puede explorar.
            Deliberadamente discreto incluso para magnitudes continuas: un
            espacio continuo invita a un ajuste fino que casi siempre es
            sobreajuste. Si un resultado aparece con periodo 27 pero no con 25
            ni con 30, no es un resultado.
        low / high: Cotas duras. Se aplican tambien a valores fijados a mano
            fuera de `choices`, porque un humano tambien se equivoca.
        description: Que controla el parametro. Alimenta la documentacion de
            configuracion, de modo que el catalogo se documente solo.
    """

    name: str
    default: Any
    choices: tuple[Any, ...] = ()
    low: float | None = None
    high: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise InvalidParameter(
                "El nombre de un parametro debe ser un identificador valido",
                name=self.name,
            )
        if self.low is not None and self.high is not None and self.low > self.high:
            raise InvalidParameter(
                "Cotas invertidas", name=self.name, low=self.low, high=self.high
            )

    def validate(self, value: Any) -> None:
        """Comprueba una asignacion concreta.

        Raises:
            InvalidParameter: si el valor cae fuera de `[low, high]`.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if self.low is not None and value < self.low:
            raise InvalidParameter(
                f"{self.name}={value} por debajo del minimo",
                name=self.name,
                value=value,
                low=self.low,
            )
        if self.high is not None and value > self.high:
            raise InvalidParameter(
                f"{self.name}={value} por encima del maximo",
                name=self.name,
                value=value,
                high=self.high,
            )

    @property
    def cardinality(self) -> int | None:
        """Numero de valores explorables, o `None` si no se declararon.

        Discovery lo agrega para informar del tamano real del espacio. Un
        espacio cuya cardinalidad se desconoce no permite afirmar que la
        busqueda fue exhaustiva, y esa honestidad debe aparecer en el informe.
        """
        return len(self.choices) if self.choices else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default": self.default,
            "choices": list(self.choices),
            "low": self.low,
            "high": self.high,
            "description": self.description,
        }


__all__ = ["ParamSpec"]
