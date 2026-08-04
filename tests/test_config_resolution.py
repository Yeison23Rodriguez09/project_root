"""Precedencia y procedencia de la configuracion.

El resolvedor es puro, asi que la precedencia se prueba exhaustivamente sin
tocar el sistema de ficheros. Eso es el valor concreto de la division de
ADR-0005: los casos raros -empates, claves desconocidas, ramas que colisionan
con hojas- se cubren en microsegundos y sin ficheros temporales.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.container.bootstrap import load_configuration
from app.core.config.provenance import (
    Priority,
    flatten,
    unflatten,
)
from app.core.config.resolver import ConfigLayer, freeze, require, resolve
from app.core.exceptions import ConfigError, ConfigValidationError

ROOT = Path(__file__).resolve().parent.parent

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


# ---------------------------------------------------------------------------
# Contratos de dominio en la cadena de resolucion (ADR-0015)
#
# Hasta esta etapa, `configs/risk.toml` y `configs/backtest.toml` no los leia
# NADIE: sus valores vivian tambien en los defectos de `RiskLimits` y en una
# constante del comando, y la fuente que mandaba era la que menos se revisa -el
# codigo- mientras el fichero documentado quedaba de adorno. Es P5 roto.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_domain_contracts_reach_the_effective_configuration() -> None:
    """Riesgo y backtest se resuelven, con su procedencia y bajo su namespace."""
    config = load_configuration(ROOT, mode="ci")

    assert config.get("risk.sizing.risk_fraction") == 0.01
    assert config.get("risk.limits.min_stop_cost_multiple") == 3.0
    assert config.get("backtest.account.initial_equity") == 10_000.0
    assert "risk.toml" in config.origin_of("risk.sizing.risk_fraction")


@pytest.mark.unit
def test_the_namespaces_keep_three_files_from_colliding() -> None:
    """Tres contratos declaran `[meta] version` y ninguno pisa al otro.

    Es el motivo del prefijo: `global.toml`, `risk.toml` y `backtest.toml`
    comparten prioridad, y sin espacio de nombres los tres escribirian la clave
    `meta.version` con la misma autoridad. La resolucion tendria que elegir entre
    valores igual de legitimos.
    """
    config = load_configuration(ROOT, mode="ci")

    assert config.get("meta.version") == 1
    assert config.get("risk.meta.version") == 1
    assert config.get("backtest.meta.version") == 1

    #  marca toda clave declarada en mas de una capa, incluida la
    # sobrescritura legitima de un perfil sobre el fichero global. Lo que no
    # puede haber es disputa entre capas de la MISMA prioridad: ahi no hay
    # criterio para elegir, y es justo lo que el prefijo evita.
    same_priority = [
        value.key
        for value in config.trace.contested()
        if any(origin.priority == value.origin.priority for _, origin in value.overridden)
    ]
    assert not same_priority, f"Contratos que se pisan con igual autoridad: {same_priority}"


@pytest.mark.unit
def test_the_prose_that_explains_a_value_is_not_a_value() -> None:
    """Las subtablas `rationale` no entran en la configuracion efectiva.

    Si entraran, entrarian tambien en el `ConfigFingerprint`: reescribir un
    comentario para aclararlo cambiaria la huella y con ella la identidad de toda
    corrida posterior, de modo que dos experimentos identicos dejarian de
    parecerlo por una correccion de estilo.
    """
    config = load_configuration(ROOT, mode="ci")

    prose = [key for key in config.trace.flat() if "rationale" in key]
    assert not prose, f"La prosa entro en la configuracion efectiva: {prose}"


@pytest.mark.unit
def test_editing_a_rationale_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    """El reciproco, comprobado sobre ficheros reales y no por razonamiento."""
    import shutil

    from app.config.providers.toml import TomlFileProvider
    from app.core.config.fingerprint import fingerprint
    from app.core.config.provenance import Priority
    from app.core.config.resolver import resolve

    def huella(path: Path) -> str:
        layer = TomlFileProvider(path, Priority.GLOBAL_FILE, prefix="risk").load()
        assert layer is not None
        trace, _ = resolve([layer])
        return str(fingerprint(trace))

    original = tmp_path / "risk.toml"
    shutil.copy(ROOT / "configs" / "risk.toml", original)
    edited = tmp_path / "risk_editado.toml"
    edited.write_text(
        original.read_text(encoding="utf-8").replace(
            "Fraccion del capital que se arriesga", "FRACCION del capital que se arriesga"
        ),
        encoding="utf-8",
    )

    assert huella(original) == huella(edited)


@pytest.mark.unit
def test_an_incomplete_risk_policy_is_rejected_instead_of_defaulted(tmp_path: Path) -> None:
    """Un `risk.toml` sin sus claves NO cae a los defectos del tipo.

    Caer en silencio a un `risk_fraction` por defecto es la forma en que una
    politica de riesgo deja de aplicarse sin que nadie borre una linea: la
    plataforma seguiria dimensionando, con numeros que nadie declaro.
    """
    import shutil

    from app.container.bootstrap import platform_services

    for name in ("global.toml", "backtest.toml"):
        shutil.copy(ROOT / "configs" / name, tmp_path / name)
    (tmp_path / "risk.toml").write_text("[meta]\nversion = 1\n", encoding="utf-8")
    (tmp_path / "symbols").mkdir()
    root = tmp_path.parent / "proyecto"
    root.mkdir(exist_ok=True)
    shutil.rmtree(root / "configs", ignore_errors=True)
    shutil.copytree(tmp_path, root / "configs")

    with pytest.raises(ConfigValidationError, match="riesgo"):
        platform_services(root)
