"""Precedencia y procedencia de la configuracion.

El resolvedor es puro, asi que la precedencia se prueba exhaustivamente sin
tocar el sistema de ficheros. Eso es el valor concreto de la division de
ADR-0005: los casos raros -empates, claves desconocidas, ramas que colisionan
con hojas- se cubren en microsegundos y sin ficheros temporales.
"""

from __future__ import annotations

import pytest

from app.core.config.provenance import (
    Priority,
    flatten,
    unflatten,
)
from app.core.config.resolver import ConfigLayer, freeze, require, resolve
from app.core.exceptions import ConfigError, ConfigValidationError

pytestmark = pytest.mark.unit


def layer(priority: Priority, locator: str, **values: object) -> ConfigLayer:
    return ConfigLayer.from_mapping(priority, locator, values)


# ---------------------------------------------------------------------------
# Aplanado
# ---------------------------------------------------------------------------


def test_flatten_and_unflatten_roundtrip() -> None:
    original = {"risk": {"max_dd": 0.2, "sizing": {"mode": "atr"}}, "seed": 7}
    flat = flatten(original)
    assert flat == {"risk.max_dd": 0.2, "risk.sizing.mode": "atr", "seed": 7}
    assert unflatten(flat) == original


def test_unflatten_rejects_leaf_and_branch_collision() -> None:
    """`risk = 1` junto a `risk.max = 2` es una incoherencia real.

    Resolverla en silencio dejaria al usuario con un valor que no puso.
    """
    with pytest.raises(ConfigError):
        unflatten({"risk": 1, "risk.max": 2})


# ---------------------------------------------------------------------------
# Precedencia
# ---------------------------------------------------------------------------


def test_higher_priority_wins_regardless_of_order() -> None:
    """El orden de carga no altera el resultado; solo la prioridad."""
    high = layer(Priority.ENVIRONMENT, "entorno", seed=99)
    low = layer(Priority.GLOBAL_FILE, "global.toml", seed=1)

    forward, _ = resolve([low, high])
    backward, _ = resolve([high, low])

    assert forward.get("seed").value == 99
    assert backward.get("seed").value == 99
    assert forward.flat() == backward.flat()


def test_provenance_records_the_winning_source() -> None:
    trace, _ = resolve(
        [
            layer(Priority.CODE_DEFAULT, "schema", seed=0),
            layer(Priority.SYMBOL_FILE, "symbols/EURUSD.toml", seed=42),
        ]
    )
    resolved = trace.get("seed")
    assert resolved.value == 42
    assert resolved.origin.priority is Priority.SYMBOL_FILE
    assert "EURUSD" in resolved.origin.locator
    assert not resolved.is_default


def test_discarded_candidates_are_kept_ordered() -> None:
    """Los descartados se conservan del mas prioritario al menos.

    Al depurar, la pregunta cara no es "de donde salio este valor" sino "por que
    NO salio el que yo puse". Guardarlos responde a la segunda.
    """
    trace, _ = resolve(
        [
            layer(Priority.CODE_DEFAULT, "schema", threshold=0.5),
            layer(Priority.GLOBAL_FILE, "global.toml", threshold=0.6),
            layer(Priority.STRATEGY_FILE, "strategies/x.toml", threshold=0.7),
            layer(Priority.COMMAND_LINE, "--set", threshold=0.9),
        ]
    )
    resolved = trace.get("threshold")
    assert resolved.value == 0.9
    assert resolved.was_contested
    assert [value for value, _origin in resolved.overridden] == [0.7, 0.6, 0.5]


def test_equal_priority_tie_warns_and_last_wins() -> None:
    """Un empate no se silencia: casi siempre son dos ficheros pisandose."""
    trace, report = resolve(
        [
            layer(Priority.SYMBOL_FILE, "symbols/a.toml", spread=1.0),
            layer(Priority.SYMBOL_FILE, "symbols/b.toml", spread=2.0),
        ]
    )
    assert trace.get("spread").value == 2.0
    assert "CONFIG_PRIORITY_TIE" in report.codes()
    assert report.ok, "Un empate avisa, no aborta: hay casos legitimos"


def test_non_default_is_the_short_summary_of_a_run() -> None:
    trace, _ = resolve(
        [
            layer(Priority.CODE_DEFAULT, "schema", a=1, b=2, c=3),
            layer(Priority.LOCAL_FILE, "local.toml", b=20),
        ]
    )
    assert [v.key for v in trace.non_default()] == ["b"]
    assert len(trace.contested()) == 1


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------


def test_unknown_key_is_an_error_with_a_hint() -> None:
    """Una clave desconocida suele ser una errata; aceptarla haria que el
    sistema ejecutara algo distinto de lo que el usuario cree."""
    known = frozenset({"risk.max_drawdown_pct"})
    _trace, report = resolve(
        [layer(Priority.GLOBAL_FILE, "global.toml", **{"limits.max_drawdown_pct": 0.3})],
        known_keys=known,
    )
    assert "CONFIG_UNKNOWN_KEY" in report.codes()
    assert not report.ok
    issue = next(i for i in report if i.code == "CONFIG_UNKNOWN_KEY")
    assert issue.context["hint"] == "risk.max_drawdown_pct"


def test_required_keys_must_be_present_and_non_null() -> None:
    trace, _ = resolve([layer(Priority.GLOBAL_FILE, "global.toml", present=1, empty=None)])
    report = require(trace, ["present", "empty", "absent"])
    assert report.codes() == {"CONFIG_NULL_KEY", "CONFIG_MISSING_KEY"}


def test_freeze_refuses_to_start_with_an_invalid_configuration() -> None:
    """Arrancar con configuracion invalida produce resultados que parecen
    validos, que es el fallo mas caro posible."""
    trace, report = resolve(
        [layer(Priority.GLOBAL_FILE, "global.toml", typo=1)],
        known_keys=frozenset({"real"}),
    )
    with pytest.raises(ConfigValidationError):
        freeze(trace, report)


def test_freeze_returns_the_effective_configuration() -> None:
    trace, report = resolve([layer(Priority.GLOBAL_FILE, "global.toml", seed=7)])
    assert freeze(trace, report) == {"seed": 7}


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def test_resolution_is_deterministic() -> None:
    """Misma entrada, misma traza. Es P1 aplicado a la configuracion."""
    layers = [
        layer(Priority.CODE_DEFAULT, "schema", a=1, b=2),
        layer(Priority.PROFILE_FILE, "profiles/dev.toml", b=3),
        layer(Priority.ENVIRONMENT, "entorno", a=9),
    ]
    first, _ = resolve(layers)
    second, _ = resolve(layers)
    assert first.to_dict() == second.to_dict()
