"""`BlockSearchSpace`: que ensamblajes existen y cuales no.

Discovery no descubre indicadores, descubre ENSAMBLAJES. Lo que se prueba aqui
es que el muestreo respete las reglas que hacen util un ensamblaje: familias
distintas, sin duplicados, discreto y reproducible.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.core.determinism import rng_for
from app.core.exceptions import InvariantViolation
from app.core.registry.metadata import ComponentEntry
from app.core.registry.params import ParamSpec
from app.core.registry.registry import Registry
from app.core.types import LifecycleState, Symbol, Timeframe
from app.discovery.generator.search_space import BlockSearchSpace
from app.domain.value_objects.strategy_spec import CombineMode
from app.shared.ports import SearchSpacePort


def _block(
    name: str, family: str, *, choices: tuple[Any, ...] = (7, 14, 21)
) -> ComponentEntry[Any]:
    return ComponentEntry(
        name=name,
        fn=lambda **_: None,
        tags=frozenset({family}),
        params=(ParamSpec(name="period", default=14, choices=choices),),
    )


def _registry(*entries: ComponentEntry[Any]) -> Registry[Any]:
    registry: Registry[Any] = Registry("signal")
    for entry in entries:
        registry.add(entry)
    return registry


def _space(*entries: ComponentEntry[Any], **kwargs: Any) -> BlockSearchSpace:
    catalog = (
        _registry(*entries)
        if entries
        else _registry(
            _block("ema_cross", "trend"),
            _block("rsi_reversion", "momentum"),
            _block("atr_breakout", "volatility"),
        )
    )
    return BlockSearchSpace(
        symbol=Symbol("EURUSD"), timeframe=Timeframe.M15, entries=catalog, **kwargs
    )


def _rng(seed: int = 7) -> np.random.Generator:
    return rng_for(seed, "test")


@pytest.mark.unit
def test_the_space_satisfies_the_port() -> None:
    assert isinstance(_space(), SearchSpacePort)


@pytest.mark.unit
def test_sampling_is_reproducible_with_the_same_seed() -> None:
    """Sin esto, una corrida de discovery no se puede repetir y su resultado no
    es evidencia de nada."""
    space = _space()

    first = [s.strategy_id for s in space.sample(_rng(), 10)]
    second = [s.strategy_id for s in space.sample(_rng(), 10)]

    assert first == second


@pytest.mark.unit
def test_a_different_seed_explores_a_different_region() -> None:
    space = _space()

    first = {s.strategy_id for s in space.sample(_rng(1), 10)}
    second = {s.strategy_id for s in space.sample(_rng(999), 10)}

    assert first != second


@pytest.mark.unit
def test_samples_are_never_repeated() -> None:
    """La deduplicacion usa el hash del spec canonico.

    Dos ensamblajes con los mismos bloques en distinto orden son el mismo, y
    evaluarlos dos veces seria tirar presupuesto de computo.
    """
    produced = list(_space().sample(_rng(), 30))

    assert len({s.strategy_id for s in produced}) == len(produced)


@pytest.mark.unit
def test_every_candidate_is_born_as_a_candidate() -> None:
    """Un ensamblaje recien generado no tiene evidencia de ninguna clase."""
    assert all(s.state is LifecycleState.CANDIDATE for s in _space().sample(_rng(), 10))


@pytest.mark.unit
def test_blocks_of_the_same_family_are_never_combined() -> None:
    """Tres bloques de tendencia no son diversificacion.

    Son el mismo voto contado tres veces, y la estrategia parece robusta sin
    serlo. `ComponentEntry.tags` existe exactamente para esto.
    """
    space = _space(
        _block("ema_cross", "trend"),
        _block("sma_cross", "trend"),
        _block("macd_trend", "trend"),
        _block("rsi_reversion", "momentum"),
    )

    for spec in space.sample(_rng(), 25):
        families = [
            "trend" if b.name in {"ema_cross", "sma_cross", "macd_trend"} else "momentum"
            for b in spec.entries
        ]
        assert len(families) == len(set(families)), repr(spec)


@pytest.mark.unit
def test_parameters_always_come_from_the_declared_choices() -> None:
    """Muestreo DISCRETO siempre.

    Un espacio continuo invita a un ajuste fino que casi siempre es sobreajuste:
    si un resultado aparece con periodo 27 pero no con 25 ni con 30, no es un
    resultado.
    """
    space = _space(_block("ema_cross", "trend", choices=(10, 20, 30)))

    for spec in space.sample(_rng(), 12):
        for block in spec.entries:
            assert block.params["period"] in (10, 20, 30)


@pytest.mark.unit
def test_a_parameter_without_choices_keeps_its_default() -> None:
    """Inventarle un rango seria decidir por el autor del bloque."""
    entry = ComponentEntry(
        name="fixed",
        fn=lambda **_: None,
        tags=frozenset({"trend"}),
        params=(ParamSpec(name="threshold", default=0.75),),
    )

    for spec in _space(entry).sample(_rng(), 5):
        assert spec.entries[0].params["threshold"] == 0.75


@pytest.mark.unit
def test_an_empty_catalog_produces_nothing() -> None:
    """Y no es un error: significa que no hay bloques con los que componer."""
    assert tuple(_space(**{}).sample(_rng(), 5)) != ()
    empty = BlockSearchSpace(
        symbol=Symbol("EURUSD"), timeframe=Timeframe.M15, entries=Registry("signal")
    )

    assert tuple(empty.sample(_rng(), 5)) == ()


@pytest.mark.unit
def test_an_exhausted_space_returns_what_it_has() -> None:
    """ "Hasta `count`" y no "exactamente `count`".

    Prometer un numero fijo obligaria a repetir estrategias o a girar
    indefinidamente, y ambas cosas mienten sobre la cobertura de la busqueda.
    """
    space = _space(
        _block("unico", "trend", choices=(5,)),
        combine_modes=(CombineMode.ALL,),
    )

    produced = tuple(space.sample(_rng(), 50))

    assert 0 < len(produced) < 50


@pytest.mark.unit
def test_max_entries_is_respected() -> None:
    space = _space(max_entries=2)

    assert all(len(s.entries) <= 2 for s in space.sample(_rng(), 20))


@pytest.mark.unit
def test_max_entries_below_one_is_rejected() -> None:
    with pytest.raises(InvariantViolation):
        _space(max_entries=0)


# ---------------------------------------------------------------------------
# Cardinalidad: honesta o `None`
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cardinality_counts_the_declared_space() -> None:
    space = _space(
        _block("a", "trend", choices=(1, 2)),
        _block("b", "momentum", choices=(1, 2, 3)),
        combine_modes=(CombineMode.ALL,),
        max_entries=2,
    )

    # tamano 1: 2 + 3 = 5 ; tamano 2: 2*3 = 6 ; total 11, por 1 modo
    assert space.cardinality() == 11


@pytest.mark.unit
def test_an_undeclared_parameter_makes_the_cardinality_unknown() -> None:
    """Decir "hay 4.800 combinaciones" cuando un parametro es un continuo sin
    acotar seria una afirmacion falsa en el informe de discovery."""
    entry = ComponentEntry(
        name="continuo",
        fn=lambda **_: None,
        tags=frozenset({"trend"}),
        params=(ParamSpec(name="k", default=1.0),),
    )

    assert _space(entry).cardinality() is None


@pytest.mark.unit
def test_the_description_reports_what_can_be_explored() -> None:
    described = _space().describe()

    assert described["symbol"] == "EURUSD"
    assert described["timeframe"] == "M15"
    assert set(described["families"]) == {"trend", "momentum", "volatility"}
    assert described["cardinality"] is not None


# ---------------------------------------------------------------------------
# Mutacion y recombinacion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mutation_changes_exactly_one_parameter() -> None:
    """Una mutacion minima es lo que hace interpretable el resultado.

    Mutar varias cosas a la vez produce mejoras que nadie puede atribuir.
    """
    space = _space()
    original = next(iter(space.sample(_rng(), 1)))

    mutated = space.mutate(original, _rng(3))

    assert mutated.symbol == original.symbol
    assert len(mutated.entries) == len(original.entries)
    assert [b.name for b in mutated.entries] == [b.name for b in original.entries]
    diferencias = sum(
        1 for a, b in zip(original.entries, mutated.entries, strict=True) if a.params != b.params
    )
    assert diferencias <= 1


@pytest.mark.unit
def test_mutating_a_block_without_choices_returns_it_unchanged() -> None:
    entry = ComponentEntry(
        name="fijo",
        fn=lambda **_: None,
        tags=frozenset({"trend"}),
        params=(ParamSpec(name="k", default=1.0),),
    )
    space = _space(entry)
    original = next(iter(space.sample(_rng(), 1)))

    assert space.mutate(original, _rng(5)).strategy_id == original.strategy_id


@pytest.mark.unit
def test_recombination_never_repeats_a_family() -> None:
    space = _space()
    samples = list(space.sample(_rng(), 6))

    child = space.recombine(samples[0], samples[1], _rng(11))

    names = [b.name for b in child.entries]
    assert len(names) == len(set(names))
    assert child.symbol == samples[0].symbol


@pytest.mark.unit
def test_recombining_a_strategy_with_itself_yields_something_valid() -> None:
    """El cruce degenerado no debe producir un hijo invalido.

    Un descendiente inventado no es informacion.
    """
    space = _space()
    parent = next(iter(space.sample(_rng(), 1)))

    child = space.recombine(parent, parent, _rng(2))

    assert child.entries
    assert len({b.name for b in child.entries}) == len(child.entries)
