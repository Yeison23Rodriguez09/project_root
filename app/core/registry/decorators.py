"""Alta declarativa de componentes.

Separado de `registry.py` porque son dos responsabilidades: el catalogo guarda,
el decorador construye la entrada a partir de una funcion. Mantenerlas juntas
obligaria a importar el mecanismo de decoracion para cargar un catalogo desde
un fichero de plugins, que es el caso de uso de bloques de terceros.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from app.core.registry.metadata import ComponentEntry
from app.core.registry.params import ParamSpec
from app.core.registry.protocols import WarmupFn
from app.core.registry.registry import Registry

T = TypeVar("T")


def register(
    registry: Registry[T],
    name: str,
    *,
    params: Sequence[ParamSpec] = (),
    warmup: WarmupFn | None = None,
    tags: Sequence[str] = (),
    description: str = "",
) -> Callable[[T], T]:
    """Decorador que da de alta una funcion en un catalogo.

    Devuelve la funcion intacta: el componente sigue siendo invocable
    directamente en un test o en un cuaderno, sin pasar por el registro. Un
    decorador que envuelve la funcion haria que lo probado y lo registrado
    fueran objetos distintos.

    Args:
        registry: Catalogo destino.
        name: Clave estable. Se persiste en los `StrategySpec`, asi que
            cambiarla invalida los artefactos que la referencian.
        params: Espacio de parametros explorable.
        warmup: Barras iniciales no calculables. Omitirlo declara cero, que es
            correcto solo para transformaciones sin ventana.
        tags: Familia del componente.
        description: Si se omite, se toma la primera linea del docstring.
    """

    def decorator(fn: T) -> T:
        doc = (getattr(fn, "__doc__", "") or "").strip().splitlines()
        registry.add(
            ComponentEntry(
                name=name,
                fn=fn,
                params=tuple(params),
                warmup_fn=warmup if warmup is not None else _zero,
                tags=frozenset(tags),
                description=description or (doc[0] if doc else ""),
            )
        )
        return fn

    return decorator


def _zero(params: Mapping[str, Any]) -> int:
    del params
    return 0


# ---------------------------------------------------------------------------
# Calculadores de calentamiento reutilizables
# ---------------------------------------------------------------------------


def warmup_from(*param_names: str, extra: int = 0) -> WarmupFn:
    """Calentamiento igual al mayor de varios parametros de periodo, mas un fijo.

    Cubre el caso habitual: un cruce de medias con periodos 20 y 50 no es
    calculable hasta la barra 50. Declararlo asi evita el error clasico de
    tomar el primer periodo y arrastrar treinta barras contaminadas al inicio
    de cada fold.
    """

    def compute(params: Mapping[str, Any]) -> int:
        values = [int(params[name]) for name in param_names if name in params]
        return (max(values) if values else 0) + extra

    return compute


def warmup_sum(*param_names: str, extra: int = 0) -> WarmupFn:
    """Calentamiento igual a la suma de varios periodos, mas un fijo.

    Es el caso de los indicadores encadenados: una media de 9 periodos sobre un
    MACD de 26 no da un valor fiable hasta la barra 35, no hasta la 26. Sumar
    es la cota conservadora y por eso es la que se declara.
    """

    def compute(params: Mapping[str, Any]) -> int:
        return sum(int(params[name]) for name in param_names if name in params) + extra

    return compute


__all__ = ["register", "warmup_from", "warmup_sum"]
