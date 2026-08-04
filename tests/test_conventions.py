"""Gobernanza de convenciones de codigo.

Aplica `configs/conventions.toml`. Igual que el test de arquitectura, no define
ni una regla propia: solo ejecuta lo declarado.

Reparto de trabajo con las demas herramientas, para no duplicar ejecucion:

* **ruff** cubre estilo, orden de imports, complejidad y modernizacion.
* **mypy** cubre el tipado.
* **este test** cubre lo que ninguno de los dos puede ver: que las dataclasses
  del dominio sean inmutables, que no haya estado mutable de modulo, que no
  entre aleatoriedad no inyectada, y los umbrales de tamano declarados con su
  razon.

La declaracion vive una sola vez, en el TOML. Eso es lo que impide que ruff
permita 88 caracteres mientras el documento promete 100.
"""

from __future__ import annotations

import ast
import tomllib
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
CONVENTIONS_FILE = ROOT / "configs" / "conventions.toml"
ARCHITECTURE_FILE = ROOT / "configs" / "architecture.toml"

MODULES = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)
IDS = [str(p.relative_to(ROOT)).replace("\\", "/") for p in MODULES]


# ---------------------------------------------------------------------------
# Carga declarativa
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def conventions() -> dict[str, Any]:
    if not CONVENTIONS_FILE.exists():
        pytest.skip(f"No existe {CONVENTIONS_FILE.name}")
    return tomllib.loads(CONVENTIONS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def layer_by_package() -> dict[str, str]:
    """Paquete corto -> capa, leido de la matriz de arquitectura.

    Se reutiliza en lugar de redeclararse: las capas son una sola verdad y vive
    en `architecture.toml`.
    """
    if not ARCHITECTURE_FILE.exists():
        return {}
    matrix = tomllib.loads(ARCHITECTURE_FILE.read_text(encoding="utf-8"))
    return {name: str(spec["layer"]) for name, spec in matrix.get("packages", {}).items()}


def limits() -> dict[str, Any]:
    return dict(conventions().get("limits", {}))


def style() -> dict[str, Any]:
    return dict(conventions().get("style", {}))


def determinism() -> dict[str, Any]:
    return dict(conventions().get("determinism", {}))


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _layer_of(path: Path) -> str:
    parts = path.relative_to(ROOT).parts
    return layer_by_package().get(parts[1], "") if len(parts) > 1 else ""


def _in_scope(rule: str, path: Path) -> bool:
    """La regla aplica a la capa de este fichero y no esta exenta."""
    scope = determinism().get("scope", {}).get(rule)
    if scope is not None and _layer_of(path) not in set(scope):
        return False
    exemptions = {
        **conventions().get("determinism", {}).get("exempt", {}),
        **conventions().get("limits", {}).get("exempt", {}),
    }
    return not exemptions.get(_relative(path), {}).get(rule, False)


def _limit_for(name: str, path: Path) -> int:
    """Umbral efectivo, considerando la excepcion nominada del fichero."""
    exempt = limits().get("exempt", {}).get(_relative(path), {})
    return int(exempt.get(name, limits().get(name, 10**9)))


@cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_dataclass(node: ast.ClassDef) -> ast.Call | ast.Name | None:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "dataclass":
            return decorator  # type: ignore[return-value]
    return None


def _has_kwarg(decorator: ast.Call | ast.Name, keyword: str) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    return any(
        kw.arg == keyword and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in decorator.keywords
    )


# ---------------------------------------------------------------------------
# 1. Tamano
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_module_length(path: Path) -> None:
    """Un modulo largo casi siempre esconde dos responsabilidades."""
    maximum = _limit_for("max_module_lines", path)
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= maximum, (
        f"{_relative(path)} tiene {lines} lineas (maximo {maximum}). "
        "Divide por responsabilidad, no por tamano."
    )


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_function_length_and_arity(path: Path) -> None:
    """Funciones cortas y con pocos argumentos.

    Mas de N argumentos indica que falta un objeto de valor, y hace casi
    imposible detectar dos parametros intercambiados por error. En una funcion
    de sizing, eso significa arriesgar el capital equivocado.
    """
    max_lines = _limit_for("max_function_lines", path)
    max_args = _limit_for("max_function_arguments", path)
    offenders: list[str] = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        length = (node.end_lineno or node.lineno) - node.lineno
        if length > max_lines:
            offenders.append(f"{node.name}: {length} lineas > {max_lines}")
        arguments = node.args
        count = (
            len(arguments.posonlyargs)
            + len(arguments.args)
            + len(arguments.kwonlyargs)
            - (1 if arguments.args and arguments.args[0].arg in ("self", "cls") else 0)
        )
        if count > max_args:
            offenders.append(f"{node.name}: {count} argumentos > {max_args}")
    assert not offenders, f"{_relative(path)}:\n  " + "\n  ".join(offenders)


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_public_class_count(path: Path) -> None:
    """Muchas clases publicas en un modulo significan que es un paquete."""
    maximum = _limit_for("max_public_classes", path)
    public = [
        node.name
        for node in _parse(path).body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]
    assert len(public) <= maximum, (
        f"{_relative(path)} expone {len(public)} clases publicas (maximo {maximum}): "
        + ", ".join(public)
    )


# ---------------------------------------------------------------------------
# 2. Estilo obligatorio
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_module_docstring(path: Path) -> None:
    """Todo modulo explica para que existe."""
    if not style().get("require_module_docstring", True):
        pytest.skip("No exigido")
    assert ast.get_docstring(_parse(path)), (
        f"{_relative(path)} no tiene docstring de modulo. Un fichero sin proposito "
        "declarado es un fichero que nadie sabe si puede borrar."
    )


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_future_annotations(path: Path) -> None:
    """`from __future__ import annotations` en todos los ficheros con codigo.

    Hace las anotaciones perezosas: permite tipar sin pagar imports en tiempo
    de ejecucion y elimina una clase entera de ciclos.
    """
    if not style().get("require_future_annotations", True):
        pytest.skip("No exigido")
    tree = _parse(path)
    has_code = any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign))
        for node in tree.body
    )
    if not has_code:
        return  # `__init__.py` de solo docstring
    present = any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in tree.body
    )
    assert present, f"{_relative(path)} no importa `annotations` de `__future__`."


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_explicit_exports(path: Path) -> None:
    """`__all__` obligatorio en modulos con simbolos publicos.

    Sin el, todo lo importado se convierte en parte accidental de la API y
    borrar un import pasa a ser un cambio incompatible.
    """
    if not style().get("require_explicit_exports", True) or path.name == "__init__.py":
        pytest.skip("No aplica")
    tree = _parse(path)
    public = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    if not public:
        return
    declared = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
        for node in tree.body
    )
    assert declared, f"{_relative(path)} expone {public} sin declarar `__all__`."


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_import_star_and_no_print(path: Path) -> None:
    """Sin `import *` y sin `print()` dentro de `app/`.

    Un `print` no lleva `run_id` y no se puede agregar: la observabilidad son
    eventos estructurados. `import *` destruye la trazabilidad de donde viene
    cada nombre.
    """
    tree = _parse(path)
    if style().get("forbid_import_star", True):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                pytest.fail(f"{_relative(path)}:{node.lineno} usa `import *`.")
    if style().get("forbid_print", True):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                pytest.fail(
                    f"{_relative(path)}:{node.lineno} usa `print()`. Usa el "
                    "EventSinkPort: un print no lleva run_id ni se puede agregar."
                )


# ---------------------------------------------------------------------------
# 3. Inmutabilidad y determinismo
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_dataclasses_are_frozen_and_slotted(path: Path) -> None:
    """Las dataclasses de las capas puras son inmutables y con `slots`.

    Un objeto de dominio mutable puede modificarse despues de validado, y
    entonces la garantia "si existe, es correcto" deja de sostenerse. `slots`
    ademas hace que una errata en el nombre de un campo falle en vez de crear
    un atributo nuevo en silencio.
    """
    require_frozen = determinism().get("require_frozen_dataclasses", True) and _in_scope(
        "require_frozen_dataclasses", path
    )
    require_slots = determinism().get("require_slots", True) and _in_scope("require_slots", path)
    if not (require_frozen or require_slots):
        pytest.skip("Fuera de alcance")

    offenders: list[str] = []
    for node in _parse(path).body:
        if not isinstance(node, ast.ClassDef):
            continue
        decorator = _is_dataclass(node)
        if decorator is None:
            continue
        if require_frozen and not _has_kwarg(decorator, "frozen"):
            offenders.append(f"{node.name}: falta frozen=True")
        if require_slots and not _has_kwarg(decorator, "slots"):
            offenders.append(f"{node.name}: falta slots=True")
    assert not offenders, f"{_relative(path)}:\n  " + "\n  ".join(offenders)


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_uninjected_randomness(path: Path) -> None:
    """Sin `random` y sin `numpy.random.seed`.

    La aleatoriedad se obtiene de `app.core.determinism.rng_for`, que deriva un
    generador aislado por consumidor. Un generador global hace que el resultado
    dependa del orden de ejecucion entre modulos.
    """
    if not (determinism().get("forbid_random", True) and _in_scope("forbid_random", path)):
        pytest.skip("Fuera de alcance")
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "random" for a in node.names):
            pytest.fail(f"{_relative(path)}:{node.lineno} importa `random`.")
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            pytest.fail(f"{_relative(path)}:{node.lineno} importa de `random`.")
        # Sobre el AST y no sobre el texto: `app/core/determinism.py` documenta
        # en su docstring por que NO usa `np.random.seed`, y una busqueda por
        # subcadena denuncia esa explicacion como si fuera la infraccion. Una
        # regla que castiga documentarse termina sin documentacion o sin regla.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func).endswith("random.seed")
        ):
            pytest.fail(
                f"{_relative(path)}:{node.lineno} llama a "
                f"{ast.unparse(node.func)}(), que muta estado global. "
                "Usa `rng_for(...)`."
            )


@pytest.mark.contract
@pytest.mark.parametrize("path", MODULES, ids=IDS)
def test_no_global_mutable_state(path: Path) -> None:
    """Ninguna variable de modulo mutable sin marcar `Final`.

    El estado compartido entre corridas es la forma mas silenciosa de romper el
    determinismo: la segunda corrida ve lo que dejo la primera. Una constante
    anotada `Final[dict[...]]` esta permitida porque declara la intencion de no
    mutarse, y mypy lo verifica.
    """
    if not (
        determinism().get("forbid_global_mutable_state", True)
        and _in_scope("forbid_global_mutable_state", path)
    ):
        pytest.skip("Fuera de alcance")

    mutable = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
    offenders: list[str] = []
    for node in _parse(path).body:
        if isinstance(node, ast.AnnAssign):
            annotation = ast.unparse(node.annotation) if node.annotation else ""
            if (
                node.value is not None
                and isinstance(node.value, mutable)
                and "Final" not in annotation
            ):
                offenders.append(f"linea {node.lineno}: {ast.unparse(node.target)}")
        elif isinstance(node, ast.Assign) and isinstance(node.value, mutable):
            names = [ast.unparse(t) for t in node.targets if not ast.unparse(t).startswith("__")]
            offenders += [f"linea {node.lineno}: {n}" for n in names]
    assert not offenders, (
        f"{_relative(path)} declara estado mutable de modulo:\n  "
        + "\n  ".join(offenders)
        + "\n  Anotalo `Final[...]` si es una constante, o muevelo a un objeto."
    )
