"""Fase 2.6 - Verificacion arquitectonica.

No prueba funcionalidad. Prueba que la arquitectura sigue siendo la que se
congelo en `architecture-baseline`.

Toda la gobernanza se lee de `configs/architecture.toml`. Este fichero no
contiene ni una sola regla propia: si la contuviera existirian dos matrices y
acabarian divergiendo, y entonces el test dejaria de comprobar lo que el
documento promete. La matriz es el dato; esto es el motor que la aplica.

Se implementa como test y no como revision manual por una razon concreta: la
erosion arquitectonica no ocurre de golpe, ocurre un import a la vez, y cada uno
parece razonable en su momento. Un humano revisando un pull request no detecta
que `app/research/features/atr.py` acaba de importar `app/broker`. El AST si.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterator
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
MATRIX_FILE = ROOT / "configs" / "architecture.toml"
PREFIX = "app"


# ---------------------------------------------------------------------------
# Carga de la matriz
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def matrix() -> dict[str, Any]:
    if not MATRIX_FILE.exists():
        pytest.fail(
            f"No existe {MATRIX_FILE.relative_to(ROOT)}. Es la fuente unica de la "
            "gobernanza: sin ella no hay nada que verificar."
        )
    return tomllib.loads(MATRIX_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def packages() -> dict[str, dict[str, Any]]:
    """Nombre corto -> especificacion, tal cual esta declarado en el TOML."""
    return dict(matrix()["packages"])


@lru_cache(maxsize=1)
def depends() -> dict[str, frozenset[str]]:
    """Nombre punteado -> conjunto cerrado de destinos permitidos."""
    return {
        f"{PREFIX}.{name}": frozenset(f"{PREFIX}.{dep}" for dep in spec.get("depends", ()))
        for name, spec in packages().items()
    }


@lru_cache(maxsize=1)
def layer_of() -> dict[str, str]:
    """Nombre punteado -> capa a la que pertenece."""
    return {f"{PREFIX}.{name}": str(spec["layer"]) for name, spec in packages().items()}


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return dict(matrix().get("rules", {}))


def rule_enabled(flag: str, *, default: bool = True) -> bool:
    value = rules().get(flag, default)
    return bool(value)


def rule_detail(section: str) -> dict[str, Any]:
    detail = rules().get(section, {})
    return dict(detail) if isinstance(detail, dict) else {}


# ---------------------------------------------------------------------------
# Recoleccion y analisis
# ---------------------------------------------------------------------------

MODULES = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)
IDS = [str(p.relative_to(ROOT)).replace("\\", "/") for p in MODULES]


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _package_of(dotted: str) -> str:
    """Paquete gobernante de un modulo, p.ej. `app.research`.

    Se prueba de mas especifico a menos para que `app.core.registry` quede bajo
    `app.core` y no bajo un hipotetico `app`.
    """
    known = depends()
    parts = dotted.split(".")
    for depth in range(min(len(parts), 3), 0, -1):
        candidate = ".".join(parts[:depth])
        if candidate in known:
            return candidate
    return PREFIX


@cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls(path: Path) -> Iterator[tuple[str, int]]:
    """(objetivo_punteado, linea) de cada llamada a un atributo del fichero.

    Solo llamadas efectivas. Nombrar `datetime.now` en un docstring o guardar la
    cadena en una tabla de reglas no es usar el reloj de pared, y confundir
    ambas cosas convierte la prohibicion en un obstaculo para documentarla.
    """
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield ast.unparse(node.func), node.lineno


def _imports(path: Path) -> Iterator[tuple[str, int, int]]:
    """(modulo, linea, nivel_relativo) de cada import del fichero."""
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, 0
        elif isinstance(node, ast.ImportFrom):
            yield (node.module or ""), node.lineno, node.level


def _resolve(dotted: str) -> Path | None:
    """Fichero que implementa un modulo `app.*`, sea modulo o paquete."""
    if not dotted.startswith(PREFIX):
        return None
    base = ROOT / Path(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 1. Matriz de dependencias
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_matrix_is_respected(path: Path) -> None:
    """Ningun paquete importa fuera de lo que la matriz le permite."""
    source = _package_of(_module_name(path))
    allowed = depends().get(source, frozenset())
    for module, line, level in _imports(path):
        if level or not module.startswith(f"{PREFIX}."):
            continue
        target = _package_of(module)
        if target == source or target in allowed:
            continue
        pytest.fail(
            f"{path.relative_to(ROOT)}:{line}\n"
            f"  {source} ({layer_of().get(source, '?')}) importa "
            f"{target} ({layer_of().get(target, '?')}) via {module}\n"
            f"  Permitido: {sorted(allowed) or 'nada'}\n"
            f"  Si la dependencia es legitima, declarala en configs/architecture.toml."
        )


@pytest.mark.contract
def test_every_package_is_governed() -> None:
    """Todo paquete de primer nivel bajo `app/` tiene fila en la matriz.

    Un paquete sin fila no esta gobernado: hoy no importa nada y manana importa
    cualquier cosa sin que nadie se entere.
    """
    if not rule_enabled("require_package_coverage"):
        pytest.skip("La matriz no exige cobertura total de paquetes")
    declared = set(packages())
    present = {
        child.name for child in APP.iterdir() if child.is_dir() and (child / "__init__.py").exists()
    }
    missing = sorted(present - declared)
    assert not missing, "Paquetes sin fila en configs/architecture.toml: " + ", ".join(missing)


@pytest.mark.contract
def test_matrix_is_internally_consistent() -> None:
    """La matriz no referencia paquetes inexistentes ni capas sin rango.

    Evita el falso verde: una fila que permite `foo` cuando `foo` no existe da
    la impresion de gobernar algo que no esta ahi.
    """
    declared = set(packages())
    ranks = set(matrix().get("layer_order", {}))

    dangling = sorted(
        f"{name} -> {dep}"
        for name, spec in packages().items()
        for dep in spec.get("depends", ())
        if dep not in declared
    )
    assert not dangling, "Destinos inexistentes: " + ", ".join(dangling)

    unranked = sorted(
        {str(spec["layer"]) for spec in packages().values() if str(spec["layer"]) not in ranks}
    )
    assert not unranked, "Capas sin rango en [layer_order]: " + ", ".join(unranked)


@pytest.mark.contract
def test_dependencies_never_climb_layers() -> None:
    """Ninguna dependencia declarada sube de capa.

    Es una comprobacion de la matriz contra si misma, independiente del codigo.
    Detecta el error humano al editarla: si alguien anade `research` a las
    dependencias de `core`, esto falla aunque ningun fichero lo use todavia.
    """
    ranks: dict[str, int] = {k: int(v) for k, v in matrix().get("layer_order", {}).items()}
    climbs = sorted(
        f"{name} ({spec['layer']}) -> {dep} ({packages()[dep]['layer']})"
        for name, spec in packages().items()
        for dep in spec.get("depends", ())
        if dep in packages() and ranks[str(packages()[dep]["layer"])] > ranks[str(spec["layer"])]
    )
    assert not climbs, "Dependencias que suben de capa:\n  " + "\n  ".join(climbs)


# ---------------------------------------------------------------------------
# 2. Ciclos
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_no_import_cycles() -> None:
    """Ningun ciclo de importacion entre modulos.

    Un ciclo A -> B -> C -> A rompe el orden de inicializacion, impide razonar
    sobre que existe cuando, y convierte cualquier refactor en cascada. Se
    comprueba a nivel de modulo y no de paquete porque ahi aparecen primero.
    """
    if not rule_enabled("forbid_cycles"):
        pytest.skip("La matriz permite ciclos")

    graph: dict[str, set[str]] = {}
    for path in MODULES:
        name = _module_name(path)
        edges: set[str] = set()
        for module, _line, level in _imports(path):
            if level or not module.startswith(f"{PREFIX}."):
                continue
            resolved = _resolve(module)
            if resolved is not None and (target := _module_name(resolved)) != name:
                edges.add(target)
        graph[name] = edges

    visited: set[str] = set()

    def walk(node: str, stack: list[str], on_stack: set[str]) -> list[str] | None:
        visited.add(node)
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.get(node, ())):
            if neighbour in on_stack:
                return [*stack[stack.index(neighbour) :], neighbour]
            if neighbour not in visited:
                found = walk(neighbour, stack, on_stack)
                if found:
                    return found
        stack.pop()
        on_stack.discard(node)
        return None

    for node in sorted(graph):
        if node in visited:
            continue
        cycle = walk(node, [], set())
        if cycle:
            pytest.fail("Ciclo de importacion:\n  " + "\n    -> ".join(cycle))


# ---------------------------------------------------------------------------
# 3. Forma de los imports
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_relative_imports(path: Path) -> None:
    """Solo se admite la forma absoluta `from app....`.

    Un import relativo cambia de significado al mover el fichero y permite que
    el mismo modulo se cargue dos veces bajo nombres distintos, con dos copias
    del estado. En un sistema que exige determinismo eso es inaceptable.
    """
    if rule_enabled("allow_relative_imports", default=False):
        pytest.skip("La matriz permite imports relativos")
    for module, line, level in _imports(path):
        if level:
            pytest.fail(
                f"{path.relative_to(ROOT)}:{line} — import relativo "
                f"`from {'.' * level}{module}`. Escribelo absoluto: `from app....`"
            )


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_legacy_import_paths(path: Path) -> None:
    """No sobrevive ninguna raiz de los arboles intermedios."""
    if not rule_enabled("forbid_legacy_import_paths"):
        pytest.skip("La matriz no prohibe rutas heredadas")
    legacy = set(rule_detail("legacy_import_paths").get("forbidden_roots", ()))
    for module, line, level in _imports(path):
        if level:
            continue
        head = module.split(".")[0]
        assert head not in legacy, (
            f"{path.relative_to(ROOT)}:{line} — `{module}` es una ruta del arbol "
            f"anterior. Debe ser `app.{module}` o su nueva ubicacion."
        )


# ---------------------------------------------------------------------------
# 4. Reglas transversales
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_third_party_confined(path: Path) -> None:
    """Las dependencias externas no cruzan hacia las capas puras.

    `pandas` fuera de los adaptadores es la via de entrada del look-ahead
    silencioso: un DataFrame reindexado desalinea sin avisar. `MetaTrader5`
    fuera de broker ata el calculo a un terminal Windows y deja la suite sin
    poder ejecutarse en CI.
    """
    if not rule_enabled("forbid_third_party_in_core"):
        pytest.skip("La matriz no confina terceros")
    package = _package_of(_module_name(path))
    short = package.removeprefix(f"{PREFIX}.")
    forbidden = set(rule_detail("third_party").get(short, ()))
    for module, line, level in _imports(path):
        if level:
            continue
        head = module.split(".")[0]
        if head in forbidden:
            pytest.fail(
                f"{path.relative_to(ROOT)}:{line} — `{head}` esta prohibido en "
                f"{package} por configs/architecture.toml."
            )


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_wall_clock(path: Path) -> None:
    """El tiempo entra por `ClockPort`, nunca por el reloj del sistema.

    Sin esto un backtest no es reproducible: cualquier regla que dependa de
    "ahora" da un resultado distinto en cada ejecucion, y las pruebas de logica
    horaria exigirian esperar al reloj real.
    """
    if not rule_enabled("forbid_wall_clock_in_pure_layers"):
        pytest.skip("La matriz no restringe el reloj de pared")
    forbidden_layers = set(rule_detail("wall_clock").get("forbidden_in_layers", ()))
    package = _package_of(_module_name(path))
    if layer_of().get(package) not in forbidden_layers:
        return
    # Se inspecciona el AST y no el texto. Una busqueda por subcadena acierta en
    # el docstring que EXPLICA la prohibicion -`app/shared/ports.py` existe
    # precisamente para que nadie llame a `datetime.now()`- y denuncia como
    # violacion la documentacion de la propia regla. Una regla que no distingue
    # su enunciado de su incumplimiento acaba desactivada, que es peor.
    for call, line in _calls(path):
        for needle in ("datetime.now", "datetime.utcnow", "time.time"):
            assert not call.endswith(needle), (
                f"{path.relative_to(ROOT)}:{line} llama a {call}() en capa "
                f"'{layer_of().get(package)}'. Inyecta ClockPort."
            )


@pytest.mark.contract
def test_no_duplicate_modules() -> None:
    """Un concepto, un fichero.

    Detecta el caso que motivo la fase 2.5: `app/domain/order.py` y
    `app/domain/entities/order.py` coexistiendo. Con dos copias, media base de
    codigo importa una y la otra media importa la otra, y ambas divergen.
    """
    if not rule_enabled("forbid_duplicate_modules"):
        pytest.skip("La matriz permite modulos duplicados")
    allowed = set(rule_detail("duplicate_modules").get("allowed_repeats", ()))
    seen: dict[str, list[str]] = {}
    for path in MODULES:
        if path.name in allowed:
            continue
        seen.setdefault(path.name, []).append(str(path.relative_to(ROOT)))
    duplicates = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert not duplicates, "Modulos duplicados:\n" + "\n".join(
        f"  {name}: {', '.join(paths)}" for name, paths in sorted(duplicates.items())
    )


@pytest.mark.contract
def test_every_package_declares_its_purpose() -> None:
    """Todo paquete tiene `__init__.py` con docstring.

    Un paquete sin proposito declarado es un paquete que nadie sabe si puede
    borrar. La regla del proyecto es que cada archivo tenga una razon; el
    `__init__.py` es donde se escribe la del paquete.
    """
    if not rule_enabled("require_docstrings"):
        pytest.skip("La matriz no exige docstrings")
    missing = [
        str(init.relative_to(ROOT))
        for init in sorted(APP.rglob("__init__.py"))
        if "__pycache__" not in init.parts and not ast.get_docstring(_parse(init))
    ]
    assert not missing, "Paquetes sin proposito declarado:\n" + "\n".join(missing)
