"""Catalogo de bloques de senal y la forma que tiene un bloque.

Un bloque de senal no es solo una funcion: es una funcion MAS la declaracion de
que features necesita. Las dos cosas viajan juntas en `Block` a proposito. Si la
declaracion viviera en una tabla aparte, podria quedarse desfasada respecto al
codigo que la usa, y el sintoma seria un bloque leyendo una feature que nadie
calculo -o peor, corriendo con la que calculo otro-.

De esa declaracion salen dos cosas que el resto del sistema necesita antes de
evaluar nada:

    que calcular    `build_frame` recibe la union de todas las peticiones y
                    computa cada indicador una sola vez
    cuanto descartar  el calentamiento del bloque es el maximo de los de sus
                    features, y de ahi sale el arranque de cada fold
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.core.registry.decorators import register
from app.core.registry.params import ParamSpec
from app.core.registry.registry import Registry
from app.domain.entities.feature_frame import FeatureFrame
from app.domain.value_objects.signal import SignalOutput
from app.research.features.frame import FeatureRequest, warmup_of

#: Declara que features necesita un bloque dados sus parametros ya resueltos.
DeclareFn = Callable[[Mapping[str, Any]], tuple[FeatureRequest, ...]]

#: Evalua el bloque sobre un frame ya construido.
EvaluateFn = Callable[..., SignalOutput]


class Block:
    """Un bloque de senal: sabe que necesita y sabe decidir con ello.

    Es invocable, asi que encaja donde se espera un `Callable[..., SignalOutput]`
    y `ComponentEntry.fn` puede guardarlo sin envoltorios adicionales.
    """

    def __init__(self, name: str, evaluate: EvaluateFn, declare: DeclareFn) -> None:
        self._name = name
        self._evaluate = evaluate
        self._declare = declare
        self.__doc__ = evaluate.__doc__

    @property
    def name(self) -> str:
        return self._name

    def features(self, params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
        """Features que este bloque leera con estos parametros."""
        return self._declare(params)

    def warmup(self, params: Mapping[str, Any]) -> int:
        """Calentamiento propio: el mayor de los de sus features."""
        return warmup_of(self._declare(params))

    def __call__(self, frame: FeatureFrame, **params: Any) -> SignalOutput:
        return self._evaluate(frame, **params)

    def __repr__(self) -> str:
        return f"Block({self._name!r})"


#: Firma almacenable de un bloque. Mismo criterio que en el catalogo de
#: features: `shared.ports.SignalBlockFn` declara `**params: Any` porque describe
#: el consumo, y un bloque concreto con firma explicita es mas estrecho que eso.
StorableBlock = Block

#: Catalogo global. Se puebla al importar `app.research.signals`.
SIGNALS: Registry[StorableBlock] = Registry("signal")

#: Roles declarados. Coinciden con los grupos de `StrategySpec` y no es
#: casualidad: un bloque solo tiene sentido en el grupo para el que se penso.
#: Un filtro colocado como entrada no generaria direccion nunca y la estrategia
#: quedaria muda sin que nada fallara.
ENTRY = "entry"
CONTEXT = "context"
EXIT = "exit"
ROLES: tuple[str, ...] = (ENTRY, CONTEXT, EXIT)


def register_block(
    name: str,
    *,
    role: str,
    declares: DeclareFn,
    params: Sequence[ParamSpec] = (),
    tags: Sequence[str] = (),
    description: str = "",
) -> Callable[[EvaluateFn], EvaluateFn]:
    """Da de alta un bloque, uniendo su declaracion de features a su evaluacion.

    Devuelve la funcion INTACTA, igual que `core.registry.register`: el bloque
    sigue siendo invocable directamente en un test sin pasar por el catalogo, de
    modo que lo probado y lo registrado no sean objetos distintos.

    Args:
        role: `entry`, `context` o `exit`. Se guarda como tag ademas de las
            propias, para que discovery pueda pedir "bloques de entrada" sin
            conocer ninguno.
    """
    if role not in ROLES:
        raise ValueError(f"Rol desconocido: {role!r}. Admitidos: {ROLES}")

    def decorator(evaluate: EvaluateFn) -> EvaluateFn:
        block = Block(name, evaluate, declares)
        register(
            SIGNALS,
            name,
            params=params,
            warmup=block.warmup,
            tags=(role, *tags),
            description=description,
        )(block)
        return evaluate

    return decorator


def feature_requests_for(blocks: Sequence[Any]) -> tuple[FeatureRequest, ...]:
    """Une las peticiones de features de una lista de `BlockSpec`.

    No deduplica: de eso se encarga `build_frame`, que es donde vive la cache.
    Los bloques desactivados se omiten -su razon de ser es poder apagarlos sin
    borrarlos- y calcular sus features seria gasto puro.
    """
    collected: list[FeatureRequest] = []
    for spec in blocks:
        if not getattr(spec, "enabled", True):
            continue
        entry = SIGNALS.get(str(spec.name))
        collected.extend(entry.fn.features(entry.resolve(spec.params)))
    return tuple(collected)


__all__ = [
    "CONTEXT",
    "ENTRY",
    "EXIT",
    "ROLES",
    "SIGNALS",
    "Block",
    "DeclareFn",
    "EvaluateFn",
    "StorableBlock",
    "feature_requests_for",
    "register_block",
]
