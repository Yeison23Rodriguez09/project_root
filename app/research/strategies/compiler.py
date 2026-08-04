"""Compilacion: de la descripcion de una estrategia a algo que se puede evaluar.

    StrategySpec  ->  compile_strategy()  ->  CompiledStrategy.evaluate(frame)

`StrategySpec` es la DESCRIPCION y ya existia: entradas, contextos, salidas,
modo de combinacion e identidad por hash de contenido. No se define ningun tipo
nuevo para describir una estrategia porque ya hay uno y duplicarlo daria dos
verdades sobre lo mismo.

Lo que no existia es el lado ejecutable, y compilar hace tres cosas que evaluar
sobre la marcha no podria:

    resuelve      cada `BlockSpec` contra el catalogo y valida sus parametros
                  UNA vez, no en cada barra ni en cada corrida de discovery
    verifica      que cada bloque esta en el grupo que le corresponde. Un filtro
                  colocado como entrada nunca generaria direccion y la estrategia
                  quedaria muda sin que nada fallara
    declara       que features hacen falta y cuanto calentamiento, antes de
                  calcular nada

El orden de evaluacion es fijo -entradas, combinacion, contextos, salidas- y no
depende del orden en que se recorran los bloques. Ningun bloque decide por si
solo el resultado final.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.registry.metadata import ComponentEntry
from app.core.types import BoolArray, DirectionArray, ReasonArray, StrategyId
from app.domain.entities.feature_frame import FeatureFrame
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec
from app.research.features.frame import FeatureRequest
from app.research.signals.contexts import allows
from app.research.signals.registry import CONTEXT, ENTRY, EXIT, SIGNALS, Block
from app.research.strategies.combine import combine


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """Lo que la estrategia decide en cada barra. Es lo que consume el backtest.

    `direction` es la direccion en que ABRIR; `exit_long` y `exit_short` dicen si
    cerrar lo que hubiera abierto. Se modelan como dos mascaras y no como una
    direccion con signo porque cerrar largos y cerrar cortos no son excluyentes:
    dos bloques de salida pueden pedir ambas cosas en la misma barra y eso no es
    una contradiccion, mientras que un unico campo obligaria a inventar una.

    `reason` explica cada barra plana. Un cero sin causa rompe el embudo de
    descarte, que es lo unico que permite responder por que una estrategia no
    opero en un tramo.
    """

    direction: DirectionArray
    exit_long: BoolArray
    exit_short: BoolArray
    reason: ReasonArray
    warmup: int

    def __post_init__(self) -> None:
        sizes = {
            "direction": self.direction.size,
            "exit_long": self.exit_long.size,
            "exit_short": self.exit_short.size,
            "reason": self.reason.size,
        }
        if len(set(sizes.values())) != 1:
            raise InvariantViolation("Decision con arrays de longitud dispar", **sizes)

    def __len__(self) -> int:
        return int(self.direction.size)

    @property
    def n_long(self) -> int:
        return int(np.count_nonzero(self.direction == 1))

    @property
    def n_short(self) -> int:
        return int(np.count_nonzero(self.direction == -1))

    def reason_histogram(self) -> dict[str, int]:
        """Embudo de descarte: cuantas barras por motivo."""
        codes, counts = np.unique(self.reason, return_counts=True)
        return {
            ReasonCode(int(code)).name: int(count)
            for code, count in zip(codes, counts, strict=True)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "bars": len(self),
            "warmup": self.warmup,
            "n_long": self.n_long,
            "n_short": self.n_short,
            "n_exit_long": int(np.count_nonzero(self.exit_long)),
            "n_exit_short": int(np.count_nonzero(self.exit_short)),
            "reasons": self.reason_histogram(),
        }


@dataclass(frozen=True, slots=True)
class _Resolved:
    """Un bloque ya localizado en el catalogo y con parametros validados.

    `ComponentEntry[Block]` y no `ComponentEntry[Any]`: el catalogo de senales ya
    esta tipado, asi que `entry.fn` es un `Block` y su `__call__` devuelve
    `SignalOutput`. Con `Any` el tipo de retorno de cada evaluacion se perderia y
    un bloque que devolviera otra cosa pasaria sin que mypy dijera nada.
    """

    entry: ComponentEntry[Block]
    params: Mapping[str, Any]
    weight: float


@dataclass(frozen=True, slots=True)
class CompiledStrategy:
    """Estrategia lista para evaluar sobre cualquier frame compatible.

    Es inmutable y no guarda estado entre evaluaciones: la misma instancia puede
    evaluar el fold 0 y el fold 7 sin que uno contamine al otro, y en vivo puede
    reevaluarse en cada vela cerrada sin reconstruirse.
    """

    spec: StrategySpec
    entries: tuple[_Resolved, ...]
    contexts: tuple[_Resolved, ...]
    exits: tuple[_Resolved, ...]
    feature_requests: tuple[FeatureRequest, ...]
    warmup: int

    @property
    def strategy_id(self) -> StrategyId:
        """Identidad de la descripcion, no de la compilacion.

        Compilar no cambia la estrategia, asi que la identidad sigue siendo la
        del `StrategySpec`: dos compilaciones del mismo spec son intercambiables.
        """
        return self.spec.strategy_id

    def evaluate(self, frame: FeatureFrame) -> StrategyDecision:
        """Decide barra a barra sobre un frame ya construido.

        Orden fijo: entradas, combinacion, veto de contextos, salidas. El
        calentamiento se aplica al final para que ninguna barra no fiable
        sobreviva a ninguna de las etapas anteriores.
        """
        bars = len(frame)
        direction, reason = combine(
            tuple(self._run(block, frame) for block in self.entries),
            mode=self.spec.combine_mode,
            threshold=self.spec.combine_threshold,
            weights=tuple(block.weight for block in self.entries),
        )
        direction, reason = self._apply_contexts(frame, direction, reason)
        exit_long, exit_short = self._apply_exits(frame, bars)

        # El calentamiento se impone aqui y no antes: un contexto podria haber
        # permitido una barra que su propia feature aun no soportaba.
        warmup = min(self.warmup, bars)
        if warmup:
            direction[:warmup] = 0
            reason[:warmup] = int(ReasonCode.WARMUP)
            exit_long[:warmup] = False
            exit_short[:warmup] = False

        return StrategyDecision(
            direction=direction,
            exit_long=exit_long,
            exit_short=exit_short,
            reason=reason,
            warmup=warmup,
        )

    # -- interno -------------------------------------------------------------

    @staticmethod
    def _run(block: _Resolved, frame: FeatureFrame) -> SignalOutput:
        return block.entry.fn(frame, **block.params)

    def _apply_contexts(
        self, frame: FeatureFrame, direction: DirectionArray, reason: ReasonArray
    ) -> tuple[DirectionArray, ReasonArray]:
        """Aplica los vetos conservando el motivo CONCRETO de cada barra.

        No se usa `SignalOutput.veto` a proposito, y la razon es informativa: ese
        metodo recibe un unico `ReasonCode` para todo el array, de modo que
        `atr_filter` -que distingue volatilidad demasiado baja de demasiado
        alta- veria sus dos vetos fundidos en uno. Esa distincion se introdujo
        para poder saber cual de los dos extremos descarta la estrategia, y
        perderla aqui la haria inutil.

        Los contextos se aplican en el orden declarado, y el primero que veta una
        barra es el que se lleva la atribucion. Con varios filtros activos, eso
        significa que el embudo cuenta cada barra una sola vez.
        """
        for block in self.contexts:
            output = self._run(block, frame)
            blocked = ~allows(output) & (direction != 0)
            if not np.any(blocked):
                continue
            direction = direction.copy()
            reason = reason.copy()
            direction[blocked] = 0
            reason[blocked] = output.reason[blocked]
        return direction, reason

    def _apply_exits(self, frame: FeatureFrame, bars: int) -> tuple[BoolArray, BoolArray]:
        """Une las salidas en OR: la primera condicion que aparece cierra.

        Asimetrico respecto a las entradas a proposito, tal y como declara
        `StrategySpec`. Para entrar se exige acuerdo; para salir basta una razon.
        Un sistema que exigiera consenso para cerrar seguiria dentro mientras los
        motivos para salir se acumulan.
        """
        exit_long = np.zeros(bars, dtype=np.bool_)
        exit_short = np.zeros(bars, dtype=np.bool_)
        for block in self.exits:
            output = self._run(block, frame)
            exit_long |= output.direction == 1
            exit_short |= output.direction == -1
        return exit_long, exit_short

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.strategy_id),
            "symbol": str(self.spec.symbol),
            "timeframe": str(self.spec.timeframe),
            "combine_mode": self.spec.combine_mode,
            "entries": [block.entry.name for block in self.entries],
            "contexts": [block.entry.name for block in self.contexts],
            "exits": [block.entry.name for block in self.exits],
            "features": sorted({name for name, _ in self.feature_requests}),
            "warmup": self.warmup,
        }


def compile_strategy(spec: StrategySpec) -> CompiledStrategy:
    """Materializa un `StrategySpec` en algo evaluable.

    Raises:
        UnknownComponent: un bloque no esta en el catalogo.
        InvalidParameter: un parametro no existe o esta fuera de sus cotas.
        InvariantViolation: un bloque esta en un grupo que no le corresponde.
    """
    entries = _resolve(spec.entries, expected=ENTRY)
    contexts = _resolve(spec.contexts, expected=CONTEXT)
    exits = _resolve(spec.exits, expected=EXIT)

    if not entries:
        # `StrategySpec` ya exige al menos una entrada, pero puede quedar sin
        # ninguna si todas estan desactivadas. Sin entradas la estrategia no
        # opera nunca, y eso debe decirse al compilar y no descubrirse en un
        # backtest que sale plano.
        raise InvariantViolation(
            "Todas las entradas estan desactivadas", strategy=str(spec.strategy_id)
        )

    requests: list[FeatureRequest] = []
    for block in (*entries, *contexts, *exits):
        requests.extend(block.entry.fn.features(block.params))

    warmup = max(
        (block.entry.warmup(block.params) for block in (*entries, *contexts, *exits)), default=0
    )

    return CompiledStrategy(
        spec=spec,
        entries=entries,
        contexts=contexts,
        exits=exits,
        feature_requests=tuple(requests),
        warmup=warmup,
    )


def _resolve(blocks: Sequence[BlockSpec], *, expected: str) -> tuple[_Resolved, ...]:
    """Localiza cada bloque y comprueba que su rol coincide con su grupo."""
    resolved: list[_Resolved] = []
    for spec in blocks:
        if not spec.enabled:
            continue
        entry = SIGNALS.get(str(spec.name))
        if expected not in entry.tags:
            role = next((tag for tag in entry.tags if tag in (ENTRY, CONTEXT, EXIT)), "?")
            raise InvariantViolation(
                "Un bloque esta en el grupo equivocado",
                block=str(spec.name),
                declared_role=role,
                placed_in=expected,
            )
        resolved.append(
            _Resolved(entry=entry, params=entry.resolve(spec.params), weight=float(spec.weight))
        )
    return tuple(resolved)


__all__ = ["CompiledStrategy", "StrategyDecision", "compile_strategy"]
