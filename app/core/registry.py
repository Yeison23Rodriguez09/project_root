"""Registro generico de componentes con espacio de parametros declarado.

Ubicacion: nivel 0. Solo depende de `app.core.exceptions`.

Por que aqui y no en `app/discovery`. El registro lo **escriben** `app/research/
features` y `app/research/signals` al darse de alta, y lo **lee** `app/discovery`
para enumerar el espacio de busqueda. Si viviera en discovery, features tendria
que importar de discovery y la dependencia quedaria invertida: el productor
dependeria del consumidor. Situarlo en `core` deja a los tres apuntando hacia
dentro.

Lo que si pertenece a discovery es todo lo que hay *sobre* este mecanismo:
`search_space`, `architecture_graph`, generador, mutador, validador y
serializador. Este modulo solo aporta el catalogo.

La pieza clave es que un componente no se registra unicamente con su funcion,
sino con la **descripcion de su espacio de parametros** y su calentamiento.
Gracias a eso el buscador enumera y muta el espacio sin conocer ningun bloque
concreto y sin que nadie mantenga a mano una lista paralela de combinaciones.
Anadir un indicador consiste en escribir la funcion y decorarla; entra en la
siguiente corrida de discovery por si solo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.core.exceptions import DuplicateComponent, UnknownComponent

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """Dominio admisible de un parametro.

    Attributes:
        name: Nombre del argumento en la funcion.
        default: Valor usado si la configuracion no lo especifica.
        choices: Conjunto discreto de valores explorables por discovery. Es
            deliberadamente discreto incluso para parametros continuos: un
            espacio continuo invita a un ajuste fino que casi siempre es
            sobreajuste. Si un resultado solo aparece con periodo 27 y no con
            25 ni con 30, no es un resultado.
        low / high: Cotas duras de validacion, aplicables tambien a valores que
            un humano fije a mano fuera de `choices`.
        description: Que controla el parametro. Alimenta la documentacion de
            configuracion, de modo que el catalogo se documente solo.
    """

    name: str
    default: Any
    choices: tuple[Any, ...] = ()
    low: float | None = None
    high: float | None = None
    description: str = ""

    def validate(self, value: Any) -> None:
        """Comprueba una asignacion concreta contra las cotas declaradas.

        Raises:
            ValueError: si el valor cae fuera de `[low, high]`.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.low is not None and value < self.low:
                raise ValueError(f"{self.name}={value} < minimo {self.low}")
            if self.high is not None and value > self.high:
                raise ValueError(f"{self.name}={value} > maximo {self.high}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default": self.default,
            "choices": list(self.choices),
            "low": self.low,
            "high": self.high,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ComponentEntry(Generic[T]):
    """Componente registrado con sus metadatos completos.

    Attributes:
        warmup_fn: Dado el diccionario de parametros, devuelve cuantas barras
            iniciales no son calculables. Es informacion critica: walk-forward
            debe descartar ese prefijo en cada fold. Sin declararlo por
            componente habria que estimarlo, y una estimacion corta contamina
            el out-of-sample con arrastre del in-sample.
        tags: Etiquetas de familia ("trend", "momentum", "volatility"). Permiten
            que discovery evite combinar tres bloques de la misma familia, que
            aportan redundancia disfrazada de diversificacion.
    """

    name: str
    fn: T
    params: tuple[ParamSpec, ...] = ()
    warmup_fn: Callable[[Mapping[str, Any]], int] = field(default=lambda _p: 0)
    tags: frozenset[str] = frozenset()
    description: str = ""

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def resolve(self, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Fusiona defaults con overrides y valida el resultado.

        Raises:
            ValueError: ante un parametro desconocido o fuera de cotas. Un
                parametro desconocido casi siempre es una errata en un fichero
                de configuracion; aceptarlo en silencio hace que la estrategia
                ejecutada no sea la que el usuario cree estar ejecutando.
        """
        resolved = self.defaults()
        if overrides:
            known = {p.name for p in self.params}
            unknown = set(overrides) - known
            if unknown:
                raise ValueError(
                    f"Parametros desconocidos para {self.name!r}: {sorted(unknown)}. "
                    f"Admitidos: {sorted(known)}"
                )
            resolved.update(overrides)
        for spec in self.params:
            spec.validate(resolved[spec.name])
        return resolved

    def warmup(self, params: Mapping[str, Any] | None = None) -> int:
        return int(self.warmup_fn(self.resolve(params)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": sorted(self.tags),
            "params": [p.to_dict() for p in self.params],
        }


class Registry(Generic[T]):
    """Catalogo nominal de componentes de un tipo.

    Falla ruidosamente ante nombres duplicados. Un nombre ambiguo invalida
    cualquier artefacto que lo referencie, porque deja de estar claro que
    codigo se ejecuto realmente.
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._entries: dict[str, ComponentEntry[T]] = {}

    # -- registro -----------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        params: Sequence[ParamSpec] = (),
        warmup: Callable[[Mapping[str, Any]], int] | None = None,
        tags: Sequence[str] = (),
        description: str = "",
    ) -> Callable[[T], T]:
        """Decorador de alta en el catalogo."""

        def decorator(fn: T) -> T:
            if name in self._entries:
                raise DuplicateComponent(
                    f"Ya existe un {self._kind} llamado {name!r}",
                    kind=self._kind,
                    name=name,
                )
            doc = (getattr(fn, "__doc__", "") or "").strip().split("\n")[0]
            self._entries[name] = ComponentEntry(
                name=name,
                fn=fn,
                params=tuple(params),
                warmup_fn=warmup if warmup is not None else (lambda _p: 0),
                tags=frozenset(tags),
                description=description or doc,
            )
            return fn

        return decorator

    # -- consulta -----------------------------------------------------------

    def get(self, name: str) -> ComponentEntry[T]:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownComponent(
                f"{self._kind} desconocido: {name!r}",
                kind=self._kind,
                name=name,
                available=sorted(self._entries),
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[ComponentEntry[T]]:
        return iter(self._entries[k] for k in sorted(self._entries))

    def names(self) -> list[str]:
        return sorted(self._entries)

    def by_tag(self, tag: str) -> list[ComponentEntry[T]]:
        return [e for e in self if tag in e.tags]

    def catalog(self) -> list[dict[str, Any]]:
        """Volcado completo del catalogo.

        Se persiste junto a cada corrida de discovery. Sin el no se puede saber
        que espacio de busqueda existia el dia que se hizo la busqueda, y una
        corrida cuyo espacio no es reconstruible no es reproducible.
        """
        return [e.to_dict() for e in self]

    def __repr__(self) -> str:
        return f"Registry({self._kind}, n={len(self)})"


__all__ = ["ComponentEntry", "ParamSpec", "Registry"]
