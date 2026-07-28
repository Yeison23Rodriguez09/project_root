"""El catalogo: almacenamiento y consulta de componentes.

Responsabilidad unica y estrecha: guardar `ComponentEntry` bajo un nombre y
devolverlos. No sabe nada de decoradores; eso vive en `decorators.py`. La
separacion no es ceremonia: permite construir un catalogo desde una fuente
externa (un fichero de plugins, una configuracion) sin pasar por el decorador,
que es como se cargaran los bloques de terceros mas adelante.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Generic, TypeVar

from app.core.registry.exceptions import DuplicateComponent, UnknownComponent
from app.core.registry.metadata import ComponentEntry

T = TypeVar("T")


class Registry(Generic[T]):
    """Catalogo nominal de componentes de un mismo tipo.

    Se instancia uno por familia: uno para features, otro para bloques de
    senal, otro para pruebas estadisticas. Mantenerlos separados evita que un
    nombre de indicador colisione con un nombre de bloque, que serian dos cosas
    distintas compitiendo por la misma clave.
    """

    __slots__ = ("_entries", "_kind")

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._entries: dict[str, ComponentEntry[T]] = {}

    # -- escritura ----------------------------------------------------------

    def add(self, entry: ComponentEntry[T]) -> ComponentEntry[T]:
        """Da de alta un componente.

        Raises:
            DuplicateComponent: si el nombre ya existe. Se falla en tiempo de
                importacion a proposito: un nombre ambiguo hace irreproducible
                cualquier artefacto que lo referencie, y descubrirlo seis meses
                despues al intentar reconstruir una estrategia es demasiado
                tarde.
        """
        if entry.name in self._entries:
            raise DuplicateComponent(
                f"Ya existe un {self._kind} llamado {entry.name!r}",
                kind=self._kind,
                name=entry.name,
            )
        self._entries[entry.name] = entry
        return entry

    # -- lectura ------------------------------------------------------------

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
        """Recorre el catalogo en orden alfabetico estable.

        El orden es determinista a proposito: discovery lo usa para enumerar el
        espacio de busqueda, y un orden dependiente de la insercion haria que
        la misma semilla produjera busquedas distintas segun el orden de
        importacion de los modulos.
        """
        return iter([self._entries[k] for k in sorted(self._entries)])

    @property
    def kind(self) -> str:
        return self._kind

    def names(self) -> list[str]:
        return sorted(self._entries)

    def by_tag(self, tag: str) -> list[ComponentEntry[T]]:
        return [e for e in self if tag in e.tags]

    def tags(self) -> set[str]:
        return {tag for entry in self._entries.values() for tag in entry.tags}

    def catalog(self) -> list[dict[str, Any]]:
        """Volcado completo, para persistir junto a cada corrida.

        Sin el no se puede saber que espacio de busqueda existia el dia en que
        se hizo la busqueda, y una corrida cuyo espacio no es reconstruible no
        es reproducible por mucho que se guarde la semilla.
        """
        return [entry.to_dict() for entry in self]

    def __repr__(self) -> str:
        return f"Registry({self._kind}, n={len(self)})"


__all__ = ["Registry"]
