"""Objetos de valor de la persistencia de series.

`WriteResult` es lo que un escritor de historicos devuelve: hechos TECNICOS de
la escritura y nada mas. No lleva proveedor, ni version, ni fecha, ni
identificador de catalogo, y esa ausencia es deliberada (ADR-0011).

    el escritor sabe        donde escribio, cuanto, cuantas barras, que huella
    el escritor NO sabe     de que broker vino, que version es, cuando fue,
                            ni bajo que identidad lo cataloga el repositorio

Quien compone esos otros datos es el servicio de almacenamiento, que si conoce
el proveedor, el reloj inyectado y el `DatasetRepositoryPort`. Si el escritor
devolviera un objeto completo tendria que recibir esa informacion, y entonces un
adaptador de infraestructura conoceria reglas de negocio.

Vive en `domain` y no en `research` porque lo devuelve un puerto declarado en
`shared`, y `shared` solo puede ver `core` y `domain`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import ContentHash


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Resultado tecnico de escribir una serie.

    Attributes:
        content_hash: Huella del CONTENIDO escrito, derivada de los datos y no
            de la ruta ni del instante. Es lo que permite detectar que una serie
            se reproceso: mismo simbolo y mismo rango con huella distinta
            significa que los numeros cambiaron, y toda comparacion contra
            resultados anteriores deja de ser valida.
        bytes_written: Tamano en disco del artefacto producido.
        bar_count: Barras efectivamente escritas. Se guarda aunque sea derivable
            de la serie: el resultado debe poder auditarse sin volver a leer el
            fichero.
        physical_location: Donde quedo, como cadena opaca. El dominio no
            interpreta rutas; solo transporta el dato para que el servicio lo
            registre.
    """

    content_hash: ContentHash
    bytes_written: int
    bar_count: int
    physical_location: str

    def __post_init__(self) -> None:
        if not str(self.content_hash).strip():
            raise InvariantViolation("Una escritura sin huella no es auditable")
        if not self.physical_location.strip():
            raise InvariantViolation("Una escritura sin ubicacion no es localizable")
        if self.bytes_written < 0:
            raise InvariantViolation(
                "bytes_written no puede ser negativo", bytes_written=self.bytes_written
            )
        if self.bar_count < 0:
            raise InvariantViolation("bar_count no puede ser negativo", bar_count=self.bar_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": str(self.content_hash),
            "bytes_written": self.bytes_written,
            "bar_count": self.bar_count,
            "physical_location": self.physical_location,
        }


__all__ = ["WriteResult"]
