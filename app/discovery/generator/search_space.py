"""Espacio de busqueda de arquitecturas, derivado del catalogo.

Implementa `SearchSpacePort`. No mantiene una lista de combinaciones en paralelo
al codigo: lee el registro y los `ParamSpec` que cada bloque declara, que es
exactamente para lo que existen -"`ParamSpec` es lo que convierte un catalogo en
un espacio de busqueda"-. Anadir un bloque al registro amplia el espacio sin
tocar este fichero.

Discovery NO descubre indicadores: descubre ENSAMBLAJES. Lo que se muestrea es
que bloques se combinan, con que parametros y bajo que modo, no la formula de un
indicador.

Muestreo DISCRETO, siempre. `ParamSpec.choices` lo impone y el motivo esta
escrito alli: un espacio continuo invita a un ajuste fino que casi siempre es
sobreajuste. Un parametro sin `choices` se deja en su valor por defecto en lugar
de inventarle un rango.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.registry.metadata import ComponentEntry
from app.core.registry.registry import Registry
from app.core.types import BlockName, Symbol, Timeframe
from app.domain.value_objects.strategy_spec import BlockSpec, CombineMode, StrategySpec

#: Numero maximo de bloques de entrada que se combinan en una estrategia.
#: Mas alla de esto la complejidad crece sin aportar: cada bloque anadido son
#: grados de libertad, y `StrategySpec.complexity` ya penaliza por ellos.
MAX_ENTRIES = 3

#: Intentos por muestra antes de rendirse. Un espacio pequeno agota sus
#: combinaciones validas y seguir sorteando no produciria nada nuevo.
MAX_ATTEMPTS_PER_SAMPLE = 20


class BlockSearchSpace:
    """Ensamblajes muestreables sobre un instrumento y un marco temporal.

    Se construye por par simbolo/timeframe porque `StrategySpec` los exige y
    porque el espacio util depende de ellos: los bloques que valen en M15 no son
    los mismos que en D1.
    """

    def __init__(
        self,
        *,
        symbol: Symbol,
        timeframe: Timeframe,
        entries: Registry[Any],
        exits: Registry[Any] | None = None,
        combine_modes: Sequence[str] = CombineMode.ALL_MODES,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise InvariantViolation("max_entries debe ser al menos 1", value=max_entries)
        self._symbol = symbol
        self._timeframe = timeframe
        self._entries = entries
        self._exits = exits
        self._combine_modes = tuple(combine_modes)
        self._max_entries = max_entries

    # -- SearchSpacePort -----------------------------------------------------

    def sample(self, rng: Any, count: int) -> Iterable[StrategySpec]:
        """Genera hasta `count` ensamblajes distintos.

        "Hasta" y no "exactamente": si el espacio se agota, se devuelven los que
        haya. Prometer un numero fijo obligaria a repetir estrategias o a girar
        indefinidamente, y ambas cosas mienten sobre la cobertura de la busqueda.

        La deduplicacion usa `strategy_id`, que es el hash del spec canonico: dos
        ensamblajes con los mismos bloques en distinto orden son el mismo, y
        gastar presupuesto evaluandolos dos veces seria tirar computo.
        """
        seen: set[str] = set()
        produced: list[StrategySpec] = []
        for _ in range(count):
            spec = self._sample_one(rng, seen)
            if spec is None:
                break
            seen.add(str(spec.strategy_id))
            produced.append(spec)
        return tuple(produced)

    def mutate(self, spec: StrategySpec, rng: Any) -> StrategySpec:
        """Cambia UN parametro de UN bloque, dejando el resto igual.

        Una mutacion minima es lo que hace interpretable el resultado: si la
        variante mejora, se sabe que lo cambio. Mutar varias cosas a la vez
        produce mejoras que nadie puede atribuir.
        """
        mutable = [i for i, block in enumerate(spec.entries) if self._choices_of(block)]
        if not mutable:
            return spec

        index = int(rng.integers(len(mutable)))
        target = spec.entries[mutable[index]]
        entry = self._entries.get(str(target.name))
        params = dict(target.params)
        candidates = [p for p in entry.params if p.choices]
        chosen = candidates[int(rng.integers(len(candidates)))]
        params[chosen.name] = chosen.choices[int(rng.integers(len(chosen.choices)))]

        entries = list(spec.entries)
        entries[mutable[index]] = BlockSpec(
            name=target.name, params=params, weight=target.weight, enabled=target.enabled
        )
        return self._rebuild(spec, entries=tuple(entries))

    def recombine(self, left: StrategySpec, right: StrategySpec, rng: Any) -> StrategySpec:
        """Toma bloques de ambos padres sin repetir familia.

        Si el cruce no produce nada valido -por ejemplo, ambos padres usan el
        mismo bloque- devuelve el primer padre en lugar de forzar un hijo
        invalido. Un descendiente inventado no es informacion.
        """
        pool = [*left.entries, *right.entries]
        rng.shuffle(pool)

        chosen: list[BlockSpec] = []
        families: set[str] = set()
        for block in pool:
            if len(chosen) >= self._max_entries:
                break
            family = self._family_of(block.name)
            if family in families or any(b.name == block.name for b in chosen):
                continue
            families.add(family)
            chosen.append(block)

        if not chosen:
            return left
        return self._rebuild(left, entries=tuple(chosen))

    def cardinality(self) -> int | None:
        """Tamano del espacio, o `None` si algun bloque no lo declara.

        Se propaga `None` en lugar de estimar: decir "hay 4.800 combinaciones"
        cuando un parametro es un continuo sin acotar seria una afirmacion falsa
        en el informe de discovery, y el informe existe para no tener que
        creerse nada.
        """
        total = 0
        for size in range(1, self._max_entries + 1):
            partial = self._combinations_of_size(size)
            if partial is None:
                return None
            total += partial
        return total * len(self._combine_modes) if total else 0

    # -- inventario ----------------------------------------------------------

    @property
    def symbol(self) -> Symbol:
        return self._symbol

    @property
    def timeframe(self) -> Timeframe:
        return self._timeframe

    def describe(self) -> dict[str, Any]:
        """Que hay en el espacio, para el informe de discovery."""
        return {
            "symbol": str(self._symbol),
            "timeframe": str(self._timeframe),
            "entry_blocks": sorted(self._entries.names()),
            "families": sorted(self._entries.tags()),
            "combine_modes": list(self._combine_modes),
            "max_entries": self._max_entries,
            "cardinality": self.cardinality(),
        }

    # -- interno -------------------------------------------------------------

    def _sample_one(self, rng: Any, seen: set[str]) -> StrategySpec | None:
        available = list(self._entries)
        if not available:
            return None

        for _ in range(MAX_ATTEMPTS_PER_SAMPLE):
            size = 1 + int(rng.integers(min(self._max_entries, len(available))))
            blocks = self._pick_entries(rng, available, size)
            if not blocks:
                continue
            mode = self._combine_modes[int(rng.integers(len(self._combine_modes)))]
            try:
                spec = StrategySpec(
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    entries=blocks,
                    exits=self._pick_exits(rng),
                    combine_mode=mode,
                    state=self._candidate_state(),
                )
            except InvariantViolation:
                # El sorteo produjo un ensamblaje que el dominio rechaza. Se
                # descarta y se vuelve a intentar: el dominio es la autoridad
                # sobre que es una estrategia valida, no el generador.
                continue
            if str(spec.strategy_id) not in seen:
                return spec
        return None

    def _pick_entries(
        self, rng: Any, available: list[ComponentEntry[Any]], size: int
    ) -> tuple[BlockSpec, ...]:
        """Elige bloques de FAMILIAS distintas.

        `ComponentEntry.tags` existe para esto: tres bloques de tendencia no son
        diversificacion, son el mismo voto contado tres veces, y una estrategia
        asi parece robusta sin serlo.
        """
        order = list(rng.permutation(len(available)))
        chosen: list[BlockSpec] = []
        families: set[str] = set()
        for position in order:
            if len(chosen) >= size:
                break
            entry = available[int(position)]
            family = self._family_of(BlockName(entry.name))
            if family in families:
                continue
            families.add(family)
            chosen.append(
                BlockSpec(name=BlockName(entry.name), params=self._sample_params(rng, entry))
            )
        return tuple(chosen)

    def _pick_exits(self, rng: Any) -> tuple[BlockSpec, ...]:
        """Un bloque de salida, o ninguno si no hay catalogo de salidas."""
        if self._exits is None or len(self._exits) == 0:
            return ()
        entries = list(self._exits)
        entry = entries[int(rng.integers(len(entries)))]
        return (BlockSpec(name=BlockName(entry.name), params=self._sample_params(rng, entry)),)

    @staticmethod
    def _sample_params(rng: Any, entry: ComponentEntry[Any]) -> dict[str, Any]:
        """Un valor por parametro, siempre de `choices`.

        Un parametro sin `choices` se queda en su defecto: inventarle un rango
        seria decidir por el autor del bloque cual es su espacio util.
        """
        params: dict[str, Any] = {}
        for spec in entry.params:
            if spec.choices:
                params[spec.name] = spec.choices[int(rng.integers(len(spec.choices)))]
            else:
                params[spec.name] = spec.default
        return params

    def _family_of(self, name: BlockName) -> str:
        """Familia de un bloque. Sin etiquetas, cada bloque es su propia familia."""
        entry = self._entries.get(str(name))
        return sorted(entry.tags)[0] if entry.tags else f"__{entry.name}"

    def _choices_of(self, block: BlockSpec) -> bool:
        return any(p.choices for p in self._entries.get(str(block.name)).params)

    def _combinations_of_size(self, size: int) -> int | None:
        """Combinaciones de `size` bloques de familias distintas."""
        by_family: dict[str, list[ComponentEntry[Any]]] = {}
        for entry in self._entries:
            by_family.setdefault(
                sorted(entry.tags)[0] if entry.tags else f"__{entry.name}", []
            ).append(entry)

        families = sorted(by_family)
        if size > len(families):
            return 0

        total = 0
        for combo in _combinations(families, size):
            product = 1
            for family in combo:
                family_total = 0
                for entry in by_family[family]:
                    if entry.cardinality is None:
                        return None
                    family_total += entry.cardinality
                product *= family_total
            total += product
        return total

    def _candidate_state(self) -> Any:
        from app.core.types import LifecycleState

        return LifecycleState.CANDIDATE

    def _rebuild(self, base: StrategySpec, *, entries: tuple[BlockSpec, ...]) -> StrategySpec:
        return StrategySpec(
            symbol=base.symbol,
            timeframe=base.timeframe,
            entries=entries,
            exits=base.exits,
            contexts=base.contexts,
            combine_mode=base.combine_mode,
            combine_threshold=base.combine_threshold,
            risk=base.risk,
            state=base.state,
            label=base.label,
        )


def _combinations(items: Sequence[str], size: int) -> Iterable[tuple[str, ...]]:
    """Combinaciones sin repeticion, en orden estable."""
    import itertools

    return itertools.combinations(items, size)


__all__ = ["MAX_ATTEMPTS_PER_SAMPLE", "MAX_ENTRIES", "BlockSearchSpace"]


def rng_from_seed(seed: int, *parts: Any) -> np.random.Generator:
    """Generador aislado para una corrida de discovery.

    Se deriva de `core.determinism.rng_for`, que es la unica fuente de
    aleatoriedad admitida: un generador global haria que el resultado dependiera
    del orden de ejecucion entre modulos.
    """
    from app.core.determinism import rng_for

    return rng_for(seed, "discovery", *parts)
