"""Gobernanza declarativa: los contratos se verifican entre si.

`test_architecture.py` comprueba que el CODIGO respete los contratos. Este
fichero comprueba que los CONTRATOS sean coherentes entre ellos y con las
decisiones registradas.

Es la pieza que impide el fallo mas insidioso de una arquitectura declarativa:
que los ficheros de gobernanza se desincronicen y sigan pasando en verde porque
nadie los cruza. Un `runtime.toml` que autoriza el paquete `promotion` en un modo
donde `architecture.toml` no lo declara, o un ADR aceptado que dice afectar a un
paquete que ya no existe, son contradicciones que ningun validador de imports ve.

Contratos verificados:

    architecture.toml   estructura y comportamiento
    runtime.toml        modos de ejecucion
    plugins.toml        entrada de codigo externo
    decisions/*.toml    decisiones, contra decisions/schema.toml
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
DECISIONS = ROOT / "decisions"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        pytest.skip(f"No existe {path.relative_to(ROOT)}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def architecture() -> dict[str, Any]:
    return _load(CONFIGS / "architecture.toml")


@lru_cache(maxsize=1)
def runtime() -> dict[str, Any]:
    return _load(CONFIGS / "runtime.toml")


@lru_cache(maxsize=1)
def plugins() -> dict[str, Any]:
    return _load(CONFIGS / "plugins.toml")


@lru_cache(maxsize=1)
def adr_schema() -> dict[str, Any]:
    return _load(DECISIONS / "schema.toml")


def declared_packages() -> dict[str, dict[str, Any]]:
    return dict(architecture().get("packages", {}))


def adr_files() -> list[Path]:
    return sorted(DECISIONS.glob("ADR-*.toml"))


@lru_cache(maxsize=1)
def adrs() -> dict[str, dict[str, Any]]:
    return {path.stem: tomllib.loads(path.read_text(encoding="utf-8")) for path in adr_files()}


# ---------------------------------------------------------------------------
# 1. Capacidades
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_every_package_declares_a_capability() -> None:
    """Cada paquete pertenece a exactamente una capacidad del sistema.

    Se habla de capacidades y no de carpetas porque una capacidad sobrevive a
    cualquier reorganizacion de directorios y esta expresada en el lenguaje del
    negocio. Un paquete que necesitara dos capacidades son dos paquetes.
    """
    known = set(architecture().get("capabilities", {}))
    problems = [
        f"{name}: capability={spec.get('capability')!r}"
        for name, spec in declared_packages().items()
        if spec.get("capability") not in known
    ]
    assert not problems, (
        "Paquetes con capacidad ausente o desconocida:\n  "
        + "\n  ".join(problems)
        + f"\n  Capacidades declaradas: {sorted(known)}"
    )


@pytest.mark.contract
def test_every_capability_is_used_and_owned() -> None:
    """Ninguna capacidad huerfana y ninguna sin responsable.

    Una capacidad que ningun paquete implementa es una promesa vacia en el mapa
    del sistema. Una sin `owner` es una capacidad que nadie mantiene.
    """
    capabilities: dict[str, dict[str, Any]] = architecture().get("capabilities", {})
    used = {spec.get("capability") for spec in declared_packages().values()}

    unused = sorted(set(capabilities) - used)
    assert not unused, "Capacidades que ningun paquete implementa: " + ", ".join(unused)

    incomplete = sorted(
        name
        for name, spec in capabilities.items()
        if not spec.get("owner") or not spec.get("purpose")
    )
    assert not incomplete, "Capacidades sin owner o sin purpose: " + ", ".join(incomplete)


@pytest.mark.contract
def test_packages_declare_purpose_and_visibility() -> None:
    """Todo paquete declara para que existe y quien puede verlo."""
    allowed_visibility = {"public", "internal"}
    problems = [
        f"{name}: purpose={bool(spec.get('purpose'))} visibility={spec.get('visibility')!r}"
        for name, spec in declared_packages().items()
        if not spec.get("purpose") or spec.get("visibility") not in allowed_visibility
    ]
    assert not problems, "Paquetes incompletos:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# 2. Contrato de comportamiento
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_behaviour_flags_are_justified() -> None:
    """Toda afirmacion de comportamiento activa lleva su motivo.

    Una regla sin justificacion es indistinguible de una regla inventada, y seis
    meses despues nadie se atreve a cambiarla ni sabe por que existe.
    """
    behaviour: dict[str, Any] = dict(architecture().get("behavior", {}))
    rationale: dict[str, Any] = dict(behaviour.pop("rationale", {}))
    missing = sorted(
        flag for flag, value in behaviour.items() if value is True and flag not in rationale
    )
    assert not missing, (
        "Afirmaciones de [behavior] sin entrada en [behavior.rationale]: "
        + ", ".join(missing)
    )


# ---------------------------------------------------------------------------
# 3. Contrato de runtime
# ---------------------------------------------------------------------------


def _effective_mode(name: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """Resuelve un modo aplicando su cadena de herencia.

    Raises:
        AssertionError: si la cadena tiene un ciclo o un padre inexistente.
    """
    modes: dict[str, dict[str, Any]] = runtime().get("modes", {})
    assert name in modes, f"Modo desconocido: {name!r}"
    assert name not in seen, f"Ciclo de herencia entre modos: {' -> '.join((*seen, name))}"
    spec = dict(modes[name])
    parent = spec.pop("inherits", None)
    if parent is None:
        return spec
    resolved = _effective_mode(str(parent), (*seen, name))
    resolved.update(spec)
    return resolved


@pytest.mark.contract
def test_mode_inheritance_resolves() -> None:
    """Toda cadena de herencia de modos termina y no se cicla."""
    for name in runtime().get("modes", {}):
        _effective_mode(name)


@pytest.mark.contract
def test_broker_orders_require_promotion_and_approval() -> None:
    """Ningun modo puede mover dinero sin validacion y sin constancia.

    Es la salvaguarda central del sistema expresada como dato. La promocion es
    tecnica; la aprobacion es humana. Confundirlas quita el ultimo control antes
    de produccion.
    """
    for name in runtime().get("modes", {}):
        effective = _effective_mode(name)
        if not effective.get("allow_broker_orders", False):
            continue
        assert effective.get("require_promoted_state", False), (
            f"El modo {name!r} permite ordenes de broker sin exigir estado promovido."
        )
        assert effective.get("require_approval_record", False), (
            f"El modo {name!r} permite ordenes de broker sin exigir registro de aprobacion."
        )


@pytest.mark.contract
def test_modes_never_discount_safeguards() -> None:
    """Un modo no puede desactivar un `require_*` que hereda activo.

    Las salvaguardas se acumulan hacia abajo. Relajar un `allow_*` es legitimo y
    visible en el diff; descontar una exigencia heredada es como se abren los
    agujeros que nadie recuerda haber abierto.
    """
    modes: dict[str, dict[str, Any]] = runtime().get("modes", {})
    offenders: list[str] = []
    for name, spec in modes.items():
        parent = spec.get("inherits")
        if parent is None:
            continue
        inherited = _effective_mode(str(parent))
        offenders += [
            f"{name}.{key} pone False lo que {parent} exige True"
            for key, value in spec.items()
            if key.startswith("require_") and value is False and inherited.get(key) is True
        ]
    assert not offenders, "Salvaguardas descontadas:\n  " + "\n  ".join(offenders)


@pytest.mark.contract
def test_runtime_references_declared_packages() -> None:
    """`allowed_packages` no autoriza paquetes que no existen en la matriz."""
    known = set(declared_packages())
    unknown = sorted(
        f"{mode}: {package}"
        for mode, packages in runtime().get("allowed_packages", {}).items()
        for package in packages
        if package not in known
    )
    assert not unknown, "Paquetes no declarados en architecture.toml:\n  " + "\n  ".join(unknown)


@pytest.mark.contract
def test_live_only_accepts_promoted_strategies() -> None:
    """En vivo solo entran estrategias promovidas o ya activas.

    Un `candidate` en produccion es la definicion exacta del fallo que este
    proyecto existe para evitar.
    """
    states: dict[str, Any] = dict(runtime().get("allowed_lifecycle_states", {}))
    states.pop("rationale", None)
    for mode in ("live", "paper"):
        allowed = set(states.get(mode, ()))
        forbidden = allowed - {"promoted", "live"}
        assert not forbidden, (
            f"El modo {mode!r} admite estados no promovidos: {sorted(forbidden)}"
        )


# ---------------------------------------------------------------------------
# 4. Contrato de plugins
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_plugin_isolation_is_coherent() -> None:
    """El aislamiento de plugins no se contradice.

    Un paquete no puede estar a la vez permitido y prohibido: la ambiguedad se
    resolveria segun el orden de evaluacion, que es la peor forma de decidir una
    frontera de seguridad.
    """
    isolation = plugins().get("isolation", {})
    allowed = set(isolation.get("allowed_imports", ()))
    forbidden = set(isolation.get("forbidden_imports", ()))
    overlap = sorted(allowed & forbidden)
    assert not overlap, "Paquetes permitidos y prohibidos a la vez: " + ", ".join(overlap)

    known = {f"app.{name}" for name in declared_packages()}
    dangling = sorted((allowed | forbidden) - known)
    assert not dangling, "Referencias a paquetes inexistentes: " + ", ".join(dangling)


@pytest.mark.contract
def test_plugin_discovery_targets_exist() -> None:
    """Los paquetes internos de descubrimiento estan bajo paquetes declarados."""
    known = set(declared_packages())
    dangling = sorted(
        dotted
        for dotted in plugins().get("discovery", {}).get("internal_packages", ())
        if dotted.split(".")[1] not in known
    )
    assert not dangling, "internal_packages fuera de la matriz: " + ", ".join(dangling)


# ---------------------------------------------------------------------------
# 5. Decisiones ejecutables
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_at_least_one_decision_exists() -> None:
    """Un proyecto con arquitectura declarativa y sin decisiones registradas es
    un proyecto que no explica por que su arquitectura es la que es."""
    assert adr_files(), "No hay ningun ADR en decisions/"


@pytest.mark.contract
@pytest.mark.parametrize("stem", sorted(adrs()), ids=sorted(adrs()))
def test_decision_matches_schema(stem: str) -> None:
    """Toda decision cumple `decisions/schema.toml`."""
    data = adrs()[stem]
    fields = adr_schema().get("fields", {})
    statuses = set(adr_schema().get("status", {}).get("allowed", ()))

    missing = sorted(set(fields.get("required", ())) - set(data))
    assert not missing, f"{stem}: campos obligatorios ausentes: {missing}"

    assert data["status"] in statuses, (
        f"{stem}: estado {data['status']!r} no admitido. Validos: {sorted(statuses)}"
    )

    assert data["id"] == stem, f"{stem}: el campo id es {data['id']!r} y no coincide con el fichero"

    try:
        date.fromisoformat(str(data["date"]))
    except ValueError:
        pytest.fail(f"{stem}: fecha {data['date']!r} no es ISO-8601")

    if data["status"] == "accepted":
        for field in adr_schema().get("fields", {}).get("required_when_accepted", ()):
            assert data.get(field), (
                f"{stem}: esta aceptada y le falta {field!r}. Una decision sin "
                "validacion es una opinion; sin reversion, una apuesta."
            )


@pytest.mark.contract
@pytest.mark.parametrize("stem", sorted(adrs()), ids=sorted(adrs()))
def test_decision_validation_is_actionable(stem: str) -> None:
    """La validacion de una decision apunta a algo concreto.

    "Se revisa en code review" sin decir que se busca no es una validacion, es
    una intencion.
    """
    data = adrs()[stem]
    if data.get("status") != "accepted":
        pytest.skip("Solo se exige a las decisiones aceptadas")
    validation = data.get("validation", {})
    kinds = set(adr_schema().get("validation", {}).get("kinds", ()))
    assert validation.get("kind") in kinds, f"{stem}: kind de validacion invalido"
    if adr_schema().get("validation", {}).get("require_reference", True):
        assert validation.get("reference"), f"{stem}: la validacion no apunta a nada"


@pytest.mark.contract
@pytest.mark.parametrize("stem", sorted(adrs()), ids=sorted(adrs()))
def test_decision_affects_real_packages(stem: str) -> None:
    """Un ADR no puede decir que afecta a paquetes que no existen.

    Es la comprobacion que convierte la documentacion en algo verificable: si un
    paquete desaparece, las decisiones que lo mencionaban se rompen y alguien
    tiene que decidir si siguen vigentes.
    """
    known = set(declared_packages())
    unknown = sorted(set(adrs()[stem].get("affected_packages", ())) - known)
    assert not unknown, f"{stem}: paquetes inexistentes en affected_packages: {unknown}"


@pytest.mark.contract
def test_decision_identifiers_are_unique_and_sequential() -> None:
    """Los identificadores no se reutilizan y no hay huecos.

    Un hueco casi siempre significa un ADR borrado. Una decision no se borra: se
    marca como `superseded` o `rejected`, porque el historial de por que se
    cambio de opinion es tan valioso como la decision vigente.
    """
    numbers = sorted(int(re.sub(r"\D", "", stem)) for stem in adrs())
    assert len(numbers) == len(set(numbers)), "Identificadores de ADR repetidos"
    expected = list(range(1, len(numbers) + 1))
    assert numbers == expected, (
        f"Secuencia de ADR con huecos: {numbers}. Una decision no se borra, se marca."
    )


# ---------------------------------------------------------------------------
# 6. La Constitucion gobierna los contratos
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def constitution() -> dict[str, str]:
    """Identificador -> titulo de cada principio irrenunciable."""
    path = ROOT / "CONSTITUTION.md"
    if not path.exists():
        pytest.fail("No existe CONSTITUTION.md. Es lo que gobierna los contratos.")
    pattern = re.compile(r"^##\s+(P\d+)\s+—\s+(.+)$", re.MULTILINE)
    return {m.group(1): m.group(2).strip() for m in pattern.finditer(path.read_text("utf-8"))}


def _citations() -> dict[str, list[str]]:
    """Principio citado -> donde se cita, recorriendo todos los contratos.

    Se recogen todas las tablas llamadas `principle` a cualquier profundidad,
    mas las claves `principle` escalares. Asi anadir una cita no exige tocar
    este test: basta declararla en el contrato.
    """
    found: dict[str, list[str]] = {}

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            if key == "principle":
                if isinstance(value, str):
                    found.setdefault(value, []).append(where)
                elif isinstance(value, dict):
                    for sub, pid in value.items():
                        if isinstance(pid, str):
                            found.setdefault(pid, []).append(f"{where}.{sub}")
            else:
                walk(value, where)

    for name, data in (
        ("architecture.toml", architecture()),
        ("runtime.toml", runtime()),
        ("plugins.toml", plugins()),
        ("decisions/schema.toml", adr_schema()),
    ):
        walk(data, name)
    return found


@pytest.mark.contract
def test_every_principle_is_invoked() -> None:
    """Todo principio de la Constitucion lo invoca al menos un contrato.

    Un principio que ningun contrato cita es retorica: suena bien y no gobierna
    nada. La cita va del contrato hacia la Constitucion, nunca al reves.
    """
    cited = set(_citations())
    declared = set(constitution())
    assert declared, "CONSTITUTION.md no declara ningun principio con formato `## P# — titulo`"
    orphans = sorted(declared - cited, key=lambda pid: int(pid[1:]))
    assert not orphans, (
        "Principios que ningun contrato invoca: "
        + ", ".join(f"{pid} ({constitution()[pid]})" for pid in orphans)
        + "\n  Cita cada uno en una tabla `principle` del contrato que lo aplica, "
        "o retiralo de la Constitucion."
    )


@pytest.mark.contract
def test_every_citation_resolves() -> None:
    """Ninguna cita apunta a un principio inexistente.

    Una regla que invoca un principio que no existe es una regla huerfana:
    parece justificada y no lo esta.
    """
    declared = set(constitution())
    dangling = sorted(
        f"{pid} citado en {', '.join(places)}"
        for pid, places in _citations().items()
        if pid not in declared
    )
    assert not dangling, "Citas sin destino en CONSTITUTION.md:\n  " + "\n  ".join(dangling)


@pytest.mark.contract
def test_derived_docs_are_in_sync() -> None:
    """Los artefactos derivados coinciden con lo que producirian sus contratos.

    Implementa P7. La documentacion es una vista: si alguien la edita a mano, o
    si cambia un contrato sin regenerar, esto rompe el build en lugar de dejar
    que el fichero siga mintiendo en silencio.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import generate_docs
    except ImportError:  # pragma: no cover
        pytest.skip("scripts/generate_docs.py no disponible")

    target = ROOT / "docs" / "ARCHITECTURE.md"
    expected = generate_docs.render()
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    assert current == expected, (
        f"{target.relative_to(ROOT)} esta desfasado respecto a los contratos.\n"
        "  Regenera con: python scripts/generate_docs.py\n"
        "  No lo edites a mano: es una vista, no una fuente (P7)."
    )


@pytest.mark.contract
def test_derived_docs_are_independent_of_the_clock() -> None:
    """Ningun generador de artefactos derivados lee el reloj de pared (ADR-0009).

    Es lo que hace verificable a `test_derived_docs_are_in_sync`. Ese test compara
    byte a byte, asi que cualquier dato tomado del tiempo fisico lo pone en rojo en
    cada cambio de dia sin que ningun contrato haya cambiado. Ocurrio: el pie del
    documento llevaba `datetime.now(UTC)`, el criterio bloqueante `docs_regenerated`
    caducaba cada 24 horas, y la respuesta aprendida fue editar el artefacto a mano
    -la senal de degradacion que BUILD.md nombra-.

    Se comprueba el AST y no el texto, por el mismo motivo que
    `test_architecture.py::test_no_wall_clock`: nombrar `datetime.now` en la prosa
    que EXPLICA la prohibicion no es incumplirla, y una regla que no distingue su
    enunciado de su violacion acaba desactivada.

    Y se comprueba el GENERADOR, no su salida. La salida contiene fechas
    legitimas -la columna `Fecha` de la tabla de decisiones sale de
    `decisions/*.toml`-, que son datos de contrato y por tanto deterministas.
    Prohibirlas en el artefacto seria confundir "no depende del reloj" con "no
    contiene fechas", y romperia al primer ADR nuevo.
    """
    forbidden = ("datetime.now", "datetime.utcnow", "date.today", "time.time", "time.time_ns")
    generators = (
        ROOT / "scripts" / "generate_docs.py",
        ROOT / "scripts" / "consolidate_architecture.py",
    )

    offenders: list[str] = []
    for path in generators:
        if not path.exists():  # pragma: no cover
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            call = ast.unparse(node.func)
            if any(call.endswith(needle) for needle in forbidden):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} llama a {call}()")

    assert not offenders, (
        "Un generador de artefactos derivados lee el reloj de pared:\n  "
        + "\n  ".join(offenders)
        + "\n  ADR-0009: el pie declara generador y version de contrato, nunca la fecha."
    )


@pytest.mark.contract
def test_constitution_changes_require_a_decision() -> None:
    """Modificar la Constitucion exige un ADR aceptado que lo respalde.

    Es el cambio mas grave que admite el proyecto (P3 + P10). Sin esta
    comprobacion, un principio podria reescribirse en un commit sin discusion,
    y con el se moverian todos los contratos que lo citan.
    """
    referencing = [
        stem
        for stem, data in adrs().items()
        if data.get("status") == "accepted"
        and "CONSTITUTION" in str(data.get("decision", "")) + str(data.get("context", ""))
    ]
    assert referencing or not constitution(), (
        "Hay principios declarados y ningun ADR aceptado que los establezca. "
        "La Constitucion no puede aparecer sin decision que la respalde."
    )


@pytest.mark.contract
def test_supersession_links_resolve() -> None:
    """`supersedes` y `superseded_by` apuntan a decisiones existentes y coherentes."""
    known = set(adrs())
    problems: list[str] = []
    for stem, data in adrs().items():
        target = data.get("supersedes")
        if target and target not in known:
            problems.append(f"{stem} reemplaza a {target}, que no existe")
        if target and adrs().get(target, {}).get("status") != "superseded":
            problems.append(
                f"{stem} reemplaza a {target}, que sigue en estado "
                f"{adrs().get(target, {}).get('status')!r}"
            )
    assert not problems, "Enlaces de reemplazo incoherentes:\n  " + "\n  ".join(problems)
