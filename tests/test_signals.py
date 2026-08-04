"""`FeatureFrame`, su construccion y los bloques de senal.

Se repite aqui la comprobacion de causalidad de `test_no_lookahead.py`, ahora un
nivel mas arriba. No es redundante: una feature causal compuesta con una
comparacion mal escrita -mirar `i+1` en vez de `i-1` al detectar un cruce-
produce un bloque que mira al futuro con features impecables. El error se
introduce al componer, asi que hay que comprobarlo al componer.

Los tests de bloque estan parametrizados sobre el CATALOGO: un bloque nuevo
queda cubierto por el hecho de registrarse.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.core.exceptions import InvariantViolation
from app.core.registry.exceptions import InvalidParameter
from app.core.registry.metadata import ComponentEntry
from app.core.types import Timeframe
from app.domain.entities.bars import Bars
from app.domain.entities.feature_frame import FeatureFrame, feature_key
from app.domain.value_objects.signal import ReasonCode
from app.research.features.frame import build_frame, warmup_of
from app.research.signals import SIGNALS, allows, ema_cross, rsi_reversion
from app.research.signals.registry import CONTEXT, ENTRY, EXIT, feature_requests_for

STEP = Timeframe.M15.nanoseconds

BLOCKS: list[ComponentEntry[Any]] = sorted(SIGNALS, key=lambda e: e.name)
IDS: list[str] = [entry.name for entry in BLOCKS]


def _bars(count: int = 600, *, seed: int = 11) -> Bars:
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0.0, 0.0005, count))
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


def _frame_for(entry: ComponentEntry[Any], bars: Bars | None = None) -> FeatureFrame:
    """Frame con exactamente lo que ese bloque declara necesitar."""
    series = bars if bars is not None else _bars()
    resolved = entry.resolve({})
    return build_frame(series, entry.fn.features(resolved))


def _evaluate(entry: ComponentEntry[Any], bars: Bars | None = None) -> Any:
    return entry.fn(_frame_for(entry, bars), **entry.resolve({}))


def _role_of(entry: ComponentEntry[Any]) -> str:
    return next(tag for tag in entry.tags if tag in (ENTRY, CONTEXT, EXIT))


# ---------------------------------------------------------------------------
# FeatureFrame
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_canonical_key_reads_like_the_indicator() -> None:
    assert feature_key("ema", {"period": 20}) == "ema_20"
    assert feature_key("atr", {"period": 14}) == "atr_14"
    assert feature_key("macd", {"fast": 12, "slow": 26}) == "macd_12_26"


@pytest.mark.unit
def test_the_key_normalises_equivalent_values() -> None:
    """`20` y `20.0` son el mismo periodo.

    Si produjeran claves distintas, la cache calcularia dos veces el mismo
    indicador y el ahorro desapareceria justo en el caso para el que existe.
    """
    assert feature_key("ema", {"period": 20.0}) == feature_key("ema", {"period": 20})


@pytest.mark.unit
def test_the_frame_rejects_a_misaligned_feature() -> None:
    bars = _bars(count=100)

    with pytest.raises(InvariantViolation):
        FeatureFrame(
            bars=bars,
            features={"ema_20": np.zeros(99, dtype=np.float64)},
            warmups={"ema_20": 19},
        )


@pytest.mark.unit
def test_the_frame_rejects_a_feature_without_declared_warmup() -> None:
    """Sin su calentamiento no puede descontarse del arranque, y las primeras
    barras del fold entrarian contaminadas."""
    bars = _bars(count=100)

    with pytest.raises(InvariantViolation):
        FeatureFrame(bars=bars, features={"ema_20": np.zeros(100)}, warmups={})


@pytest.mark.unit
def test_the_frame_is_immutable() -> None:
    """Un bloque que escribiera sobre una feature contaminaria a los demas, que
    la comparten por diseno."""
    bars = _bars(count=100)
    origen = np.arange(100, dtype=np.float64)
    frame = FeatureFrame(bars=bars, features={"x_1": origen}, warmups={"x_1": 0})

    with pytest.raises(ValueError, match="read-only"):
        frame["x_1"][0] = 999.0

    origen[0] = 999.0
    assert frame["x_1"][0] == 0.0, "el frame copio en vez de referenciar"


@pytest.mark.unit
def test_the_frame_warmup_is_the_maximum() -> None:
    """Un cruce de 20 y 50 no es evaluable hasta la 50; cualquier otro agregado
    arrastraria barras contaminadas."""
    frame = build_frame(_bars(), [("ema", {"period": 20}), ("ema", {"period": 50})])

    assert frame.warmups == {"ema_20": 19, "ema_50": 49}
    assert frame.warmup == 49


@pytest.mark.unit
def test_asking_for_a_feature_that_is_not_there_names_the_alternatives() -> None:
    frame = build_frame(_bars(), [("ema", {"period": 20})])

    with pytest.raises(InvariantViolation) as error:
        frame["ema_50"]

    assert error.value.context["available"] == ["ema_20"]


@pytest.mark.unit
def test_require_lists_every_missing_feature_at_once() -> None:
    """Quien depura prefiere una pasada a cinco."""
    frame = build_frame(_bars(), [("ema", {"period": 20})])

    with pytest.raises(InvariantViolation) as error:
        frame.require("ema_20", "rsi_14", "atr_14")

    assert error.value.context["missing"] == ["rsi_14", "atr_14"]


# ---------------------------------------------------------------------------
# Construccion y cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_same_feature_is_computed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Es la razon de ser del contenedor.

    Cinco bloques que usan `ema_20` la comparten, y discovery evalua miles de
    combinaciones: recalcularla es el gasto que el frame existe para evitar.

    Cuenta invocaciones reales y no claves resultantes porque son cosas
    distintas: recalcular y sobrescribir produce el mismo mapa final, asi que un
    test sobre `frame.features` no distinguiria una cache de su ausencia. Para
    contarlas hay que sustituir la entrada del catalogo, que es inmutable, de ahi
    el `dataclasses.replace` sobre el almacen del registro.
    """
    import dataclasses

    from app.research.features.registry import FEATURES

    llamadas = {"n": 0}
    original = FEATURES.get("ema")

    def contando(bars: Bars, **params: Any) -> Any:
        llamadas["n"] += 1
        return original.fn(bars, **params)

    monkeypatch.setitem(
        FEATURES._entries,
        "ema",
        dataclasses.replace(original, fn=contando),
    )

    frame = build_frame(
        _bars(),
        [("ema", {"period": 20}), ("ema", {"period": 20.0}), ("ema", {"period": 20})],
    )

    assert llamadas["n"] == 1, "la cache no evito el recalculo"
    assert set(frame.features) == {"ema_20"}


@pytest.mark.unit
def test_an_unknown_parameter_fails_before_computing_anything() -> None:
    """Una errata no puede acabar en un indicador corriendo con su defecto."""
    with pytest.raises(InvalidParameter):
        build_frame(_bars(), [("ema", {"periodd": 20})])


@pytest.mark.unit
def test_the_aggregate_warmup_needs_no_computation() -> None:
    """Permite saber cuantas barras descartar antes de gastar un ciclo."""
    peticiones = [("ema", {"period": 20}), ("atr", {"period": 14}), ("sma", {"period": 100})]

    assert warmup_of(peticiones) == 99
    assert warmup_of([]) == 0


# ---------------------------------------------------------------------------
# Contrato de los bloques
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_a_block_cannot_see_the_future(entry: ComponentEntry[Any]) -> None:
    """El error se introduce al COMPONER, no en la feature.

    Una feature causal mas una comparacion mal escrita -mirar `i+1` en vez de
    `i-1` al detectar un cruce- produce un bloque que mira al futuro con
    indicadores impecables.

    Los cortes NO son arbitrarios, y es lo que hace util al test. Una fuga de un
    solo compas se manifiesta unicamente en la ULTIMA barra del prefijo, y solo
    si esa barra decidia algo: con cortes fijos, ambos lados salen planos en una
    barra cualquiera, coinciden, y la fuga pasa entera. Se corta justo despues
    de cada barra en que la salida CAMBIA, que son las unicas donde hay algo que
    contrastar.
    """
    bars = _bars()
    params = entry.resolve({})
    completa = entry.fn(_frame_for(entry, bars), **params)

    cambios = np.flatnonzero(
        (completa.direction[1:] != completa.direction[:-1])
        | (completa.reason[1:] != completa.reason[:-1])
    )
    assert cambios.size > 0, f"{entry.name} produce una salida constante: nada que contrastar"

    cortes = sorted({int(i) + 2 for i in cambios[:: max(1, cambios.size // 25)]})
    for cut in cortes:
        if cut > len(bars):
            continue
        truncada = entry.fn(_frame_for(entry, bars.slice(0, cut)), **params)
        np.testing.assert_array_equal(
            truncada.direction,
            completa.direction[:cut],
            err_msg=f"{entry.name} mira al futuro: truncar en {cut} cambio decisiones anteriores",
        )
        np.testing.assert_array_equal(truncada.reason, completa.reason[:cut])


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_a_block_is_aligned_with_the_bars(entry: ComponentEntry[Any]) -> None:
    bars = _bars()
    salida = _evaluate(entry, bars)

    assert salida.direction.size == len(bars)
    assert salida.strength.size == len(bars)
    assert salida.reason.size == len(bars)


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_a_directional_bar_always_says_ok(entry: ComponentEntry[Any]) -> None:
    """Lo exige `SignalOutput`: si se decide operar, el motivo es OK."""
    salida = _evaluate(entry)
    actuando = salida.direction != 0

    if actuando.any():
        assert np.all(salida.reason[actuando] == int(ReasonCode.OK))


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_the_warmup_is_flat_and_says_so(entry: ComponentEntry[Any]) -> None:
    """`configs/plugins.toml` exige codigos de razon: un bloque que devuelve
    ceros sin motivo rompe el embudo de descarte."""
    salida = _evaluate(entry)
    calentamiento = entry.warmup(entry.resolve({}))

    assert calentamiento > 0, f"{entry.name} no declara calentamiento"
    assert np.all(salida.direction[:calentamiento] == 0)
    assert np.all(salida.reason[:calentamiento] == int(ReasonCode.WARMUP))


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_never_a_bar_without_a_reason(entry: ComponentEntry[Any]) -> None:
    """Cada barra plana explica por que lo esta."""
    salida = _evaluate(entry)
    planas = salida.direction == 0

    assert np.all(np.isin(salida.reason[planas], [int(c) for c in ReasonCode]))


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_a_block_is_deterministic(entry: ComponentEntry[Any]) -> None:
    bars = _bars()
    primera = _evaluate(entry, bars)

    for _ in range(3):
        otra = _evaluate(entry, bars)
        np.testing.assert_array_equal(otra.direction, primera.direction)
        np.testing.assert_array_equal(otra.reason, primera.reason)


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_every_block_declares_role_features_and_parameters(
    entry: ComponentEntry[Any],
) -> None:
    resolved = entry.resolve({})

    assert _role_of(entry) in (ENTRY, CONTEXT, EXIT)
    assert entry.fn.features(resolved), f"{entry.name} no declara features"
    assert entry.params, f"{entry.name} no declara parametros explorables"
    assert entry.description, f"{entry.name} no describe que hace"


@pytest.mark.contract
@pytest.mark.parametrize("entry", BLOCKS, ids=IDS)
def test_the_declared_features_are_exactly_the_ones_used(
    entry: ComponentEntry[Any],
) -> None:
    """Un frame con lo declarado y NADA mas tiene que bastar.

    Si el bloque leyera una feature que no declaro, aqui fallaria con
    `InvariantViolation` en vez de en produccion con un frame incompleto.
    """
    _evaluate(entry)  # `_frame_for` construye solo lo declarado.


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry", [e for e in BLOCKS if CONTEXT in e.tags], ids=lambda e: str(e.name)
)
def test_a_context_never_proposes_a_direction(entry: ComponentEntry[Any]) -> None:
    """Es la diferencia con una entrada, y no es de matiz: una entrada dice
    "aqui hay una oportunidad", un contexto dice "esta no cuenta"."""
    salida = _evaluate(entry)

    assert np.all(salida.direction == 0)
    assert np.all(salida.strength == 0.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry", [e for e in BLOCKS if CONTEXT in e.tags], ids=lambda e: str(e.name)
)
def test_a_context_permits_and_vetoes_with_a_motive(entry: ComponentEntry[Any]) -> None:
    salida = _evaluate(entry)
    permitidas = allows(salida)

    assert permitidas.any(), f"{entry.name} no permite ni una barra"
    assert not permitidas.all(), f"{entry.name} no veta ninguna: no esta filtrando"
    vetadas = salida.reason[~permitidas & (salida.reason != int(ReasonCode.WARMUP))]
    assert np.all(vetadas >= int(ReasonCode.FILTERED_SESSION))


@pytest.mark.unit
@pytest.mark.parametrize("entry", [e for e in BLOCKS if ENTRY in e.tags], ids=lambda e: str(e.name))
def test_an_entry_actually_fires(entry: ComponentEntry[Any]) -> None:
    """Un bloque de entrada que nunca propone nada no es un bloque."""
    salida = _evaluate(entry, _bars(count=2000))

    assert salida.n_long + salida.n_short > 0


# ---------------------------------------------------------------------------
# Aritmetica de cada bloque
# ---------------------------------------------------------------------------


def _crossing_bars() -> Bars:
    """Serie con un cruce al alza inequivoco y despues uno a la baja."""
    close = np.concatenate(
        [np.linspace(1.20, 1.00, 120), np.linspace(1.00, 1.30, 120), np.linspace(1.30, 1.05, 120)]
    )
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, close.size * STEP, STEP, dtype=np.int64),
        open=close,
        high=close + 0.0005,
        low=close - 0.0005,
        close=close,
        volume=np.full(close.size, 1.0),
    )


@pytest.mark.unit
def test_the_cross_is_an_event_and_not_a_state() -> None:
    """Devolver "rapida por encima de lenta" produciria una senal en cada barra
    de la tendencia y la estrategia intentaria entrar continuamente."""
    bars = _crossing_bars()
    frame = build_frame(bars, [("ema", {"period": 10}), ("ema", {"period": 30})])

    salida = ema_cross(frame, fast=10, slow=30)

    assert salida.n_long >= 1
    assert salida.n_short >= 1
    # Un puñado de eventos, no cientos de barras en estado.
    assert salida.n_long + salida.n_short < 20


@pytest.mark.unit
def test_a_fast_slower_than_the_slow_is_flat_by_design() -> None:
    """Discovery explora combinaciones: este caso debe descartarse solo, con
    motivo visible, en vez de lanzar."""
    bars = _bars()
    frame = build_frame(bars, [("ema", {"period": 50}), ("ema", {"period": 20})])

    salida = ema_cross(frame, fast=50, slow=20)

    assert np.all(salida.direction == 0)
    assert np.all(salida.reason == int(ReasonCode.FLAT_BY_DESIGN))


@pytest.mark.unit
def test_the_reversion_enters_on_leaving_the_zone_not_on_entering_it() -> None:
    """Comprar porque el RSI cayo de 30 es comprar mientras sigue cayendo."""
    bajada = np.linspace(1.30, 1.00, 200)
    subida = np.linspace(1.00, 1.15, 100)
    close = np.concatenate([bajada, subida])
    bars = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, close.size * STEP, STEP, dtype=np.int64),
        open=close,
        high=close + 0.0005,
        low=close - 0.0005,
        close=close,
        volume=np.full(close.size, 1.0),
    )
    frame = build_frame(bars, [("rsi", {"period": 14})])
    valores = frame["rsi_14"]

    salida = rsi_reversion(frame, period=14, oversold=30.0, overbought=70.0)
    largos = np.flatnonzero(salida.direction == 1)

    assert largos.size >= 1
    for indice in largos:
        assert valores[indice] > 30.0, "entro dentro de la zona, no al salir"
        assert valores[indice - 1] <= 30.0


@pytest.mark.unit
def test_the_two_volatility_vetoes_are_told_apart() -> None:
    """Existen por motivos opuestos y contarlos juntos ocultaria cual descarta."""
    from app.research.signals import atr_filter

    bars = _bars(count=800)
    frame = build_frame(bars, [("atr", {"period": 14}), ("sma", {"period": 100})])

    estrecho = atr_filter(frame, period=14, baseline=100, min_ratio=0.8, max_ratio=5.0)
    ancho = atr_filter(frame, period=14, baseline=100, min_ratio=0.0, max_ratio=0.01)

    assert int(ReasonCode.FILTERED_VOLATILITY_LOW) in set(estrecho.reason.tolist())
    assert int(ReasonCode.FILTERED_VOLATILITY_HIGH) in set(ancho.reason.tolist())


@pytest.mark.unit
def test_an_exit_names_the_direction_it_closes() -> None:
    """`+1` significa "cierra los largos", no "abre un largo"."""
    from app.research.signals import ema_exit

    bars = _crossing_bars()
    frame = build_frame(bars, [("ema", {"period": 20})])
    close = np.asarray(bars.close)
    media = frame["ema_20"]

    salida = ema_exit(frame, period=20)

    for indice in np.flatnonzero(salida.direction == 1):
        assert close[indice] <= media[indice], "cerrar largos exige cruce a la baja"
    for indice in np.flatnonzero(salida.direction == -1):
        assert close[indice] > media[indice]


# ---------------------------------------------------------------------------
# El catalogo
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_catalogue_covers_the_three_roles() -> None:
    for role in (ENTRY, CONTEXT, EXIT):
        assert SIGNALS.by_tag(role), f"no hay ningun bloque de rol {role}"


@pytest.mark.unit
def test_the_requests_of_a_strategy_are_gathered_and_deduplicated() -> None:
    from app.domain.value_objects.strategy_spec import BlockSpec

    bloques = [
        BlockSpec(name="ema_cross", params={"fast": 12, "slow": 50}),  # type: ignore[arg-type]
        BlockSpec(name="ema_exit", params={"period": 50}),  # type: ignore[arg-type]
        BlockSpec(name="rsi_filter", params={"period": 14}, enabled=False),  # type: ignore[arg-type]
    ]

    peticiones = feature_requests_for(bloques)
    frame = build_frame(_bars(), peticiones)

    # `ema_50` la piden dos bloques y se calcula una vez; el desactivado no pide.
    assert set(frame.features) == {"ema_12", "ema_50"}


@pytest.mark.unit
def test_a_disabled_block_costs_nothing() -> None:
    """Su razon de ser es poder apagarlo sin borrarlo; calcular sus features
    seria gasto puro."""
    from app.domain.value_objects.strategy_spec import BlockSpec

    activo = BlockSpec(name="atr_filter", params={})  # type: ignore[arg-type]
    apagado = BlockSpec(name="atr_filter", params={}, enabled=False)  # type: ignore[arg-type]

    assert feature_requests_for([activo])
    assert feature_requests_for([apagado]) == ()
