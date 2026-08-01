"""Compilacion y evaluacion de estrategias.

Tres propiedades sostienen el fichero:

`test_a_compiled_strategy_cannot_see_the_future` -- la causalidad otra vez, un
nivel mas arriba. Bloques causales combinados con una reduccion mal escrita
pueden producir una estrategia que mira al futuro, y el error se introduce al
combinar.

`test_a_block_in_the_wrong_group_is_rejected_at_compile_time` -- un filtro
colocado como entrada nunca generaria direccion. La estrategia quedaria muda y
el backtest saldria plano sin que nada fallara.

`test_no_bar_is_ever_flat_without_a_motive` -- un cero sin causa rompe el embudo
de descarte, que es lo unico que permite responder por que no se opero.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import InvariantViolation
from app.core.registry.exceptions import UnknownComponent
from app.core.types import Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.domain.value_objects.strategy_spec import BlockSpec, CombineMode, StrategySpec
from app.research.features.frame import build_frame
from app.research.strategies import CompiledStrategy, compile_strategy
from app.research.strategies.combine import combine

STEP = Timeframe.M15.nanoseconds


def _bars(count: int = 900, *, seed: int = 3) -> Bars:
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0.0, 0.0006, count))
    opening = close - rng.normal(0.0, 0.0001, count)
    margin = np.abs(rng.normal(0.0, 0.0003, count)) + 0.00005
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, count * STEP, STEP, dtype=np.int64),
        open=opening,
        high=np.maximum(opening, close) + margin,
        low=np.minimum(opening, close) - margin,
        close=close,
        volume=np.full(count, 100.0),
    )


def _block(name: str, **params: Any) -> BlockSpec:
    return BlockSpec(name=name, params=params)  # type: ignore[arg-type]


def _spec(
    *,
    entries: tuple[BlockSpec, ...] = (),
    contexts: tuple[BlockSpec, ...] = (),
    exits: tuple[BlockSpec, ...] = (),
    mode: str = CombineMode.ALL,
    threshold: float = 0.5,
) -> StrategySpec:
    return StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=entries or (_block("ema_cross"),),
        contexts=contexts,
        exits=exits,
        combine_mode=mode,
        combine_threshold=threshold,
    )


def _run(spec: StrategySpec, bars: Bars | None = None) -> Any:
    compiled = compile_strategy(spec)
    series = bars if bars is not None else _bars()
    return compiled.evaluate(build_frame(series, compiled.feature_requests))


def _signal(block: str, directions: list[int], reasons: list[int]) -> SignalOutput:
    """Salida fabricada a mano, para aislar la logica de combinacion."""
    return SignalOutput(
        block=block,  # type: ignore[arg-type]
        direction=np.array(directions, dtype=np.int8),
        strength=np.where(np.array(directions) != 0, 1.0, 0.0).astype(np.float64),
        reason=np.array(reasons, dtype=np.int16),
    )


OK = int(ReasonCode.OK)
NO = int(ReasonCode.NO_SETUP)
THRESH = int(ReasonCode.THRESHOLD_NOT_REACHED)


# ---------------------------------------------------------------------------
# La descripcion ya existia
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_description_is_the_existing_domain_type() -> None:
    """No se define un tipo nuevo para describir una estrategia.

    `StrategySpec` ya describe entradas, contextos, salidas, modo de combinacion
    e identidad por contenido. Un `StrategyDefinition` paralelo daria dos
    verdades sobre lo mismo, y la que se serializa en artefactos es esta.
    """
    compiled = compile_strategy(_spec())

    assert isinstance(compiled.spec, StrategySpec)
    assert compiled.strategy_id == compiled.spec.strategy_id


@pytest.mark.unit
def test_compiling_twice_gives_interchangeable_strategies() -> None:
    """Compilar no cambia la estrategia: la identidad sigue siendo la del spec."""
    spec = _spec()
    primera, segunda = compile_strategy(spec), compile_strategy(spec)

    assert primera.strategy_id == segunda.strategy_id
    assert primera.to_dict() == segunda.to_dict()


# ---------------------------------------------------------------------------
# Lo que compilar detecta y evaluar no podria
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_block_in_the_wrong_group_is_rejected_at_compile_time() -> None:
    """Un filtro como entrada nunca generaria direccion: la estrategia quedaria
    muda y el backtest saldria plano sin que nada fallara."""
    with pytest.raises(InvariantViolation) as error:
        compile_strategy(_spec(entries=(_block("rsi_filter"),)))

    assert error.value.context["declared_role"] == "context"
    assert error.value.context["placed_in"] == "entry"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("group", "block"),
    [("contexts", "ema_cross"), ("exits", "atr_filter"), ("contexts", "ema_exit")],
    ids=["entrada como contexto", "contexto como salida", "salida como contexto"],
)
def test_every_group_checks_the_role(group: str, block: str) -> None:
    with pytest.raises(InvariantViolation):
        compile_strategy(_spec(**{group: (_block(block),)}))  # type: ignore[arg-type]


@pytest.mark.unit
def test_an_unknown_block_fails_at_compile_time() -> None:
    with pytest.raises(UnknownComponent):
        compile_strategy(_spec(entries=(_block("no_existe"),)))


@pytest.mark.unit
def test_a_strategy_with_every_entry_disabled_is_rejected() -> None:
    """Sin entradas no opera nunca, y eso debe decirse al compilar en vez de
    descubrirse en un backtest que sale plano."""
    spec = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={}, enabled=False),),  # type: ignore[arg-type]
    )

    with pytest.raises(InvariantViolation, match="desactivadas"):
        compile_strategy(spec)


@pytest.mark.unit
def test_compilation_declares_features_and_warmup_before_computing() -> None:
    """Permite saber que calcular y cuanto descartar sin gastar un ciclo."""
    compiled = compile_strategy(
        _spec(
            entries=(_block("ema_cross", fast=12, slow=50),),
            contexts=(_block("atr_filter", period=14, baseline=100),),
            exits=(_block("ema_exit", period=20),),
        )
    )

    nombres = {name for name, _ in compiled.feature_requests}
    assert nombres == {"ema", "atr", "sma"}
    assert compiled.warmup == 99  # sma_100 manda sobre ema_50 y atr_14


# ---------------------------------------------------------------------------
# Combinacion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_conjunction_needs_every_block_to_agree() -> None:
    a = _signal("a", [1, 1, 0, -1], [OK, OK, NO, OK])
    b = _signal("b", [1, 0, 0, -1], [OK, NO, NO, OK])

    direction, reason = combine((a, b), mode=CombineMode.ALL, threshold=0.5, weights=(1.0, 1.0))

    np.testing.assert_array_equal(direction, [1, 0, 0, -1])
    assert reason[0] == OK


@pytest.mark.unit
def test_the_propagated_motive_is_the_blocking_one_and_not_a_generic_zero() -> None:
    """El motivo tiene que ser el del bloque que bloqueo, no un relleno.

    Se usan codigos DISTINTOS de `NO_SETUP` a proposito: con el neutro, una
    implementacion que rellenara todo con `NO_SETUP` daria el mismo resultado
    que una que propaga de verdad, y el test no distinguiria una de otra.
    """
    filtrado = int(ReasonCode.FILTERED_VOLATILITY_LOW)
    confirmacion = int(ReasonCode.CONFIRMATION_MISSING)

    #                      barra:  0      1          2              3
    a = _signal("a", [1, 0, 1, 0], [OK, filtrado, OK, filtrado])
    b = _signal("b", [1, 1, 0, 0], [OK, OK, confirmacion, confirmacion])

    _, reason = combine((a, b), mode=CombineMode.ALL, threshold=0.5, weights=(1.0, 1.0))

    assert reason[0] == OK
    assert reason[1] == filtrado, "no propago el motivo del bloque que bloqueo"
    assert reason[2] == confirmacion
    # La barra 3 la bloquean los DOS con motivos distintos: es la unica que
    # distingue "el primero" de "el ultimo", y el orden declarado forma parte de
    # la identidad de la estrategia, asi que la atribucion tiene que ser estable.
    assert reason[3] == filtrado, "con dos bloqueos manda el primero declarado"


@pytest.mark.unit
def test_disjunction_needs_only_one() -> None:
    a = _signal("a", [1, 0, 0], [OK, NO, NO])
    b = _signal("b", [0, -1, 0], [NO, OK, NO])

    direction, _ = combine((a, b), mode=CombineMode.ANY, threshold=0.5, weights=(1.0, 1.0))

    np.testing.assert_array_equal(direction, [1, -1, 0])


@pytest.mark.unit
@pytest.mark.parametrize("mode", [CombineMode.ALL, CombineMode.ANY, CombineMode.MAJORITY])
def test_a_contradiction_is_never_resolved_by_precedence(mode: str) -> None:
    """Una estrategia que se contradice es un defecto de diseno y debe verse en
    el embudo, no taparse eligiendo el bloque que aparece antes."""
    a = _signal("a", [1], [OK])
    b = _signal("b", [-1], [OK])

    direction, reason = combine((a, b), mode=mode, threshold=0.5, weights=(1.0, 1.0))

    assert direction[0] == 0
    assert reason[0] == int(ReasonCode.CONFLICTING_BLOCKS)


@pytest.mark.unit
def test_the_majority_does_not_break_a_tie() -> None:
    """Romperlo con cualquier criterio inventaria una decision que ningun bloque
    tomo."""
    votos = (
        _signal("a", [1], [OK]),
        _signal("b", [1], [OK]),
        _signal("c", [-1], [OK]),
        _signal("d", [-1], [OK]),
    )

    direction, reason = combine(votos, mode=CombineMode.MAJORITY, threshold=0.5, weights=(1.0,) * 4)

    assert direction[0] == 0
    assert reason[0] == int(ReasonCode.CONFLICTING_BLOCKS)


@pytest.mark.unit
def test_the_majority_wins_by_one_vote() -> None:
    votos = (
        _signal("a", [1], [OK]),
        _signal("b", [1], [OK]),
        _signal("c", [-1], [OK]),
    )

    direction, _ = combine(votos, mode=CombineMode.MAJORITY, threshold=0.5, weights=(1.0,) * 3)

    assert direction[0] == 1


@pytest.mark.unit
def test_the_weighted_mode_lets_conviction_outweigh_numbers() -> None:
    """Es el unico modo que usa la intensidad y no solo el signo."""
    convencido = SignalOutput(
        block="a",  # type: ignore[arg-type]
        direction=np.array([1], dtype=np.int8),
        strength=np.array([1.0]),
        reason=np.array([OK], dtype=np.int16),
    )
    tibio = SignalOutput(
        block="b",  # type: ignore[arg-type]
        direction=np.array([-1], dtype=np.int8),
        strength=np.array([0.1]),
        reason=np.array([OK], dtype=np.int16),
    )

    direction, _ = combine(
        (convencido, tibio), mode=CombineMode.WEIGHTED, threshold=0.4, weights=(1.0, 1.0)
    )

    assert direction[0] == 1  # (1.0 - 0.1) / 2 = 0.45 >= 0.40


@pytest.mark.unit
def test_the_weighted_mode_says_when_the_threshold_was_the_reason() -> None:
    debil = SignalOutput(
        block="a",  # type: ignore[arg-type]
        direction=np.array([1], dtype=np.int8),
        strength=np.array([0.2]),
        reason=np.array([OK], dtype=np.int16),
    )

    direction, reason = combine((debil,), mode=CombineMode.WEIGHTED, threshold=0.9, weights=(1.0,))

    assert direction[0] == 0
    assert reason[0] == THRESH


@pytest.mark.unit
def test_the_threshold_means_the_same_with_two_blocks_as_with_ten() -> None:
    """La puntuacion se normaliza por el peso total: si no, anadir bloques
    cambiaria el significado del umbral sin tocarlo.

    El caso decisivo es el ACUERDO PARCIAL, y no el unanime: con diez bloques de
    acuerdo, normalizar o no da lo mismo porque ambos superan cualquier umbral.
    Con tres de diez, normalizar da 0.3 -por debajo de 0.5, no se opera- mientras
    que no normalizar da 3.0 y dispararia. Ahi es donde se ve si el umbral
    significa "fraccion de conviccion" o "numero de bloques".
    """
    unanime = tuple(_signal(f"a{i}", [1], [OK]) for i in range(10))
    parcial = tuple(_signal(f"b{i}", [1 if i < 3 else 0], [OK if i < 3 else NO]) for i in range(10))
    pesos = (1.0,) * 10

    acuerdo, _ = combine(unanime, mode=CombineMode.WEIGHTED, threshold=0.5, weights=pesos)
    minoria, _ = combine(parcial, mode=CombineMode.WEIGHTED, threshold=0.5, weights=pesos)

    assert acuerdo[0] == 1, "diez de diez tiene que disparar"
    assert minoria[0] == 0, "tres de diez es 0.3 de conviccion: por debajo de 0.5"

    # Y el mismo 30% con un solo bloque tibio se comporta igual.
    tibio = SignalOutput(
        block="c",  # type: ignore[arg-type]
        direction=np.array([1], dtype=np.int8),
        strength=np.array([0.3]),
        reason=np.array([OK], dtype=np.int16),
    )
    solo, _ = combine((tibio,), mode=CombineMode.WEIGHTED, threshold=0.5, weights=(1.0,))
    assert solo[0] == 0


@pytest.mark.unit
def test_combining_nothing_is_rejected() -> None:
    with pytest.raises(InvariantViolation):
        combine((), mode=CombineMode.ALL, threshold=0.5, weights=())


@pytest.mark.unit
def test_an_unknown_mode_is_rejected() -> None:
    with pytest.raises(InvariantViolation):
        combine((_signal("a", [0], [NO]),), mode="magico", threshold=0.5, weights=(1.0,))


# ---------------------------------------------------------------------------
# Contextos: vetan conservando el motivo concreto
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_context_only_removes_and_never_adds() -> None:
    """Un filtro no puede crear una operacion donde no la habia."""
    bars = _bars()
    sin_filtro = _run(_spec(entries=(_block("ema_cross"),)), bars)
    con_filtro = _run(_spec(entries=(_block("ema_cross"),), contexts=(_block("rsi_filter"),)), bars)

    actua_sin = sin_filtro.direction != 0
    actua_con = con_filtro.direction != 0

    assert np.all(~actua_con | actua_sin), "el filtro creo operaciones que no existian"
    assert con_filtro.n_long + con_filtro.n_short <= sin_filtro.n_long + sin_filtro.n_short


@pytest.mark.unit
def test_the_veto_keeps_the_specific_motive_and_not_a_generic_one() -> None:
    """`SignalOutput.veto` recibe UN codigo para todo el array y fundiria los dos
    vetos de volatilidad, que se separaron para saber cual descarta.

    Aqui se comprueba que la razon que llega a la decision es la que emitio el
    contexto en esa barra concreta.
    """
    bars = _bars(count=1500)
    decision = _run(
        _spec(
            entries=(_block("ema_cross"),),
            contexts=(_block("atr_filter", min_ratio=0.04, max_ratio=0.05),),
        ),
        bars,
    )
    motivos = set(decision.reason_histogram())

    assert {"FILTERED_VOLATILITY_LOW", "FILTERED_VOLATILITY_HIGH"} & motivos, (
        f"el veto perdio el motivo concreto: {motivos}"
    )


@pytest.mark.unit
def test_the_first_context_that_vetoes_gets_the_attribution() -> None:
    """Con varios filtros activos el embudo cuenta cada barra una sola vez."""
    bars = _bars(count=1500)
    decision = _run(
        _spec(
            entries=(_block("ema_cross"),),
            contexts=(_block("rsi_filter"), _block("atr_filter")),
        ),
        bars,
    )

    assert sum(decision.reason_histogram().values()) == len(bars)


# ---------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exits_are_combined_in_or() -> None:
    """Para entrar se exige acuerdo; para salir basta una razon."""
    bars = _bars()
    una = _run(_spec(exits=(_block("ema_exit"),)), bars)
    dos = _run(_spec(exits=(_block("ema_exit"), _block("rsi_exit"))), bars)

    assert np.all(una.exit_long <= dos.exit_long)
    assert int(np.count_nonzero(dos.exit_long)) >= int(np.count_nonzero(una.exit_long))


@pytest.mark.unit
def test_closing_longs_and_shorts_are_not_exclusive() -> None:
    """Dos bloques pueden pedir ambas cosas y no es una contradiccion; un unico
    campo con signo obligaria a inventar una."""
    decision = _run(_spec(exits=(_block("ema_exit"), _block("rsi_exit"))), _bars(count=1500))

    assert decision.exit_long.dtype == np.bool_
    assert decision.exit_short.dtype == np.bool_
    assert int(np.count_nonzero(decision.exit_long)) > 0
    assert int(np.count_nonzero(decision.exit_short)) > 0


@pytest.mark.unit
def test_a_strategy_without_exits_still_compiles() -> None:
    """Legitimo: la proteccion puede venir solo del stop que coloca ejecucion."""
    decision = _run(_spec(exits=()))

    assert not decision.exit_long.any()
    assert not decision.exit_short.any()


# ---------------------------------------------------------------------------
# Propiedades de la decision
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_a_compiled_strategy_cannot_see_the_future() -> None:
    """Bloques causales mas una reduccion mal escrita producen una estrategia
    que mira al futuro. El error se introduce al combinar.

    Se corta justo despues de cada barra en que la decision cambia: una fuga de
    un compas solo se ve en la ultima barra del prefijo, y solo si decidia algo.
    """
    spec = _spec(
        entries=(_block("ema_cross"),),
        contexts=(_block("rsi_filter"),),
        exits=(_block("ema_exit"),),
    )
    compiled = compile_strategy(spec)
    bars = _bars()
    completa = compiled.evaluate(build_frame(bars, compiled.feature_requests))

    cambios = np.flatnonzero(
        (completa.direction[1:] != completa.direction[:-1])
        | (completa.reason[1:] != completa.reason[:-1])
        | (completa.exit_long[1:] != completa.exit_long[:-1])
    )
    assert cambios.size > 0

    for indice in cambios[:: max(1, cambios.size // 20)]:
        cut = int(indice) + 2
        if cut > len(bars):
            continue
        trozo = bars.slice(0, cut)
        truncada = compiled.evaluate(build_frame(trozo, compiled.feature_requests))
        np.testing.assert_array_equal(
            truncada.direction,
            completa.direction[:cut],
            err_msg=f"la estrategia mira al futuro: truncar en {cut} cambio decisiones",
        )
        np.testing.assert_array_equal(truncada.reason, completa.reason[:cut])
        np.testing.assert_array_equal(truncada.exit_long, completa.exit_long[:cut])


@pytest.mark.contract
def test_no_bar_is_ever_flat_without_a_motive() -> None:
    """Un cero sin causa rompe el embudo de descarte."""
    decision = _run(
        _spec(
            entries=(_block("ema_cross"), _block("rsi_reversion")),
            contexts=(_block("rsi_filter"), _block("atr_filter")),
            exits=(_block("ema_exit"),),
            mode=CombineMode.ANY,
        ),
        _bars(count=2000),
    )

    validos = {int(code) for code in ReasonCode}
    assert set(decision.reason.tolist()) <= validos
    assert np.all(decision.reason[decision.direction != 0] == OK)


@pytest.mark.contract
def test_the_warmup_leaves_no_decision_behind() -> None:
    """Se impone al final: un contexto podria haber permitido una barra que su
    propia feature aun no soportaba."""
    decision = _run(_spec(entries=(_block("ema_cross"),), contexts=(_block("atr_filter"),)))

    assert decision.warmup == 99
    assert np.all(decision.direction[: decision.warmup] == 0)
    assert np.all(decision.reason[: decision.warmup] == int(ReasonCode.WARMUP))
    assert not decision.exit_long[: decision.warmup].any()


@pytest.mark.contract
def test_the_same_strategy_always_decides_the_same() -> None:
    spec = _spec(entries=(_block("ema_cross"),), contexts=(_block("rsi_filter"),))
    bars = _bars()

    primera = _run(spec, bars)
    for _ in range(3):
        otra = _run(spec, bars)
        np.testing.assert_array_equal(otra.direction, primera.direction)
        np.testing.assert_array_equal(otra.reason, primera.reason)
        np.testing.assert_array_equal(otra.exit_long, primera.exit_long)


@pytest.mark.unit
def test_one_instance_can_evaluate_many_folds_without_contaminating() -> None:
    """En vivo se reevalua en cada vela cerrada sin reconstruir la estrategia."""
    compiled = compile_strategy(_spec(entries=(_block("ema_cross"),)))
    bars = _bars()

    primero = compiled.evaluate(build_frame(bars.slice(0, 400), compiled.feature_requests))
    segundo = compiled.evaluate(build_frame(bars.slice(400, 800), compiled.feature_requests))
    repetido = compiled.evaluate(build_frame(bars.slice(0, 400), compiled.feature_requests))

    np.testing.assert_array_equal(primero.direction, repetido.direction)
    assert len(segundo) == 400


@pytest.mark.unit
def test_the_decision_is_auditable() -> None:
    decision = _run(
        _spec(entries=(_block("ema_cross"),), contexts=(_block("rsi_filter"),)),
        _bars(count=1500),
    )
    volcado = decision.to_dict()

    assert volcado["bars"] == 1500
    assert volcado["warmup"] == 49
    assert sum(volcado["reasons"].values()) == 1500
    assert volcado["n_long"] + volcado["n_short"] == volcado["reasons"].get("OK", 0)


@pytest.mark.unit
def test_a_frame_without_the_declared_features_is_rejected() -> None:
    """Evaluar con un frame incompleto tiene que fallar aqui y no producir una
    decision construida sobre features que no estan."""
    compiled = compile_strategy(_spec(entries=(_block("ema_cross", fast=12, slow=50),)))
    incompleto = build_frame(_bars(), [("ema", {"period": 12})])

    with pytest.raises(InvariantViolation):
        compiled.evaluate(incompleto)


@pytest.mark.unit
def test_compiled_strategies_are_immutable() -> None:
    compiled = compile_strategy(_spec())

    with pytest.raises(FrozenInstanceError):
        compiled.warmup = 5  # type: ignore[misc]


@pytest.mark.unit
def test_the_evaluation_order_does_not_depend_on_block_order() -> None:
    """Ningun bloque decide por si solo el resultado final.

    Reordenar contextos no puede cambiar QUE barras se vetan -solo a quien se
    atribuye el veto-, porque el veto es una conjuncion.
    """
    bars = _bars(count=1500)
    izquierda = _run(
        _spec(
            entries=(_block("ema_cross"),),
            contexts=(_block("rsi_filter"), _block("atr_filter")),
        ),
        bars,
    )
    derecha = _run(
        _spec(
            entries=(_block("ema_cross"),),
            contexts=(_block("atr_filter"), _block("rsi_filter")),
        ),
        bars,
    )

    np.testing.assert_array_equal(izquierda.direction, derecha.direction)


@pytest.mark.unit
def test_the_compiled_strategy_reports_what_it_is() -> None:
    compiled: CompiledStrategy = compile_strategy(
        _spec(
            entries=(_block("ema_cross"),),
            contexts=(_block("rsi_filter"),),
            exits=(_block("ema_exit"),),
        )
    )
    volcado = compiled.to_dict()

    assert volcado["entries"] == ["ema_cross"]
    assert volcado["contexts"] == ["rsi_filter"]
    assert volcado["exits"] == ["ema_exit"]
    assert volcado["combine_mode"] == CombineMode.ALL
    assert volcado["symbol"] == "EURUSD"
