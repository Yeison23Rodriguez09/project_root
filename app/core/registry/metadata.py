"""Metadatos de un componente registrado.

`ComponentEntry` es la unidad que guarda el catalogo. Contiene la funcion, pero
sobre todo contiene lo que la rodea: su espacio de parametros, su calentamiento
y su familia. Esa envoltura es la que permite que discovery trabaje sin conocer
ningun bloque concreto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.core.registry.exceptions import InvalidParameter
from app.core.registry.params import ParamSpec
from app.core.registry.protocols import WarmupFn

T = TypeVar("T")


def _zero_warmup(params: Mapping[str, Any]) -> int:
    """Calentamiento nulo. Valor por defecto explicito y nombrado.

    Se define como funcion con nombre en lugar de un `lambda` en el `field`
    para que aparezca legible en los volcados y en los mensajes de error.
    """
    del params
    return 0


@dataclass(frozen=True, slots=True)
class ComponentEntry(Generic[T]):
    """Componente registrado con sus metadatos completos.

    Attributes:
        name: Clave estable del catalogo. Es lo que se persiste en un
            `StrategySpec`, asi que cambiarla invalida los artefactos previos.
        fn: La implementacion.
        params: Espacio de parametros declarado.
        warmup_fn: Barras iniciales no calculables, en funcion de los parametros.
        tags: Familia del componente ("trend", "momentum", "volatility"...).
            Permiten que discovery evite combinar tres bloques de la misma
            familia, que aportan redundancia disfrazada de diversificacion.
        description: Primera linea del docstring si no se indica otra cosa.
    """

    name: str
    fn: T
    params: tuple[ParamSpec, ...] = ()
    warmup_fn: WarmupFn = field(default=_zero_warmup)
    tags: frozenset[str] = frozenset()
    description: str = ""

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def resolve(self, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Fusiona defaults con overrides y valida el resultado.

        Raises:
            InvalidParameter: ante un parametro desconocido o fuera de cotas.
                Un parametro desconocido casi siempre es una errata en un
                fichero de configuracion; aceptarlo en silencio hace que la
                estrategia ejecutada no sea la que el usuario cree ejecutar.
        """
        resolved = self.defaults()
        if overrides:
            known = {p.name for p in self.params}
            unknown = set(overrides) - known
            if unknown:
                raise InvalidParameter(
                    f"Parametros desconocidos para {self.name!r}",
                    component=self.name,
                    unknown=sorted(unknown),
                    allowed=sorted(known),
                )
            resolved.update(overrides)
        for spec in self.params:
            spec.validate(resolved[spec.name])
        return resolved

    def warmup(self, params: Mapping[str, Any] | None = None) -> int:
        """Calentamiento efectivo para una parametrizacion concreta."""
        return int(self.warmup_fn(self.resolve(params)))

    @property
    def cardinality(self) -> int | None:
        """Combinaciones explorables, o `None` si algun parametro no lo declara.

        Se propaga `None` en lugar de ignorar el parametro sin `choices`: decir
        "hay 480 combinaciones" cuando en realidad hay un continuo sin acotar
        seria una afirmacion falsa en el informe de discovery.
        """
        total = 1
        for spec in self.params:
            if spec.cardinality is None:
                return None
            total *= spec.cardinality
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": sorted(self.tags),
            "cardinality": self.cardinality,
            "params": [p.to_dict() for p in self.params],
        }


__all__ = ["ComponentEntry"]
