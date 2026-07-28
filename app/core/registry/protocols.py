"""Contratos estructurales del subsistema de registro.

Se declaran aparte del resto para que un componente pueda comprobarse contra el
contrato sin importar la implementacion del catalogo. Es lo que permite que
`app.research.features` declare que cumple `WarmupFn` sin arrastrar el registro
entero a su grafo de imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WarmupFn(Protocol):
    """Calcula cuantas barras iniciales no son calculables.

    Es informacion critica, no un detalle: walk-forward descarta ese prefijo en
    cada fold. Si un componente declara un calentamiento menor del real, el
    out-of-sample arranca con valores contaminados por el in-sample y la
    validacion deja de significar nada. Por eso se declara por componente y se
    contrasta contra el calentamiento observado en los tests.
    """

    def __call__(self, params: Mapping[str, Any]) -> int: ...


@runtime_checkable
class Describable(Protocol):
    """Objeto que sabe volcarse a una forma serializable y estable.

    Todo lo que entra en un artefacto debe implementarlo. Sin una forma
    canonica, el hash de contenido no es reproducible y la corrida deja de ser
    auditable.
    """

    def to_dict(self) -> dict[str, Any]: ...


__all__ = ["Describable", "WarmupFn"]
