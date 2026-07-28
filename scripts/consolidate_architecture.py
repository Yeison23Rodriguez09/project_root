"""Fase 2.5 - Consolidacion arquitectonica. Ejecutor unico e idempotente.

Deja el repositorio con un solo arbol, sin duplicados, sin codigo muerto y con
todos los imports apuntando a `app.*`. Usa `git mv` para conservar el historial.

Uso:

    python scripts/consolidate_architecture.py              # plan, no toca nada
    python scripts/consolidate_architecture.py --apply      # ejecuta
    python scripts/consolidate_architecture.py --apply --tag  # ejecuta y etiqueta

Sin `--apply` imprime exactamente lo que haria y termina. Esa es la conducta
por defecto a proposito: una consolidacion que mueve treinta ficheros y
reescribe imports en todo el arbol no debe poder dispararse por accidente.

El script es idempotente. Si un movimiento ya se hizo, lo detecta y lo salta,
de modo que una ejecucion interrumpida se puede reanudar sin dejar el
repositorio a medias.

El tag `architecture-baseline` solo se crea si `compileall` y `pytest` pasan.
Etiquetar un arbol que no compila convertiria el punto de partida de la
plataforma en una referencia inservible.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 1. Arbol congelado
# ---------------------------------------------------------------------------

#: Paquete -> responsabilidad de una linea. El script crea el directorio y un
#: `__init__.py` con esa frase. Un paquete sin proposito declarado no se crea.
PACKAGES: dict[str, str] = {
    "app": "Capas de la arquitectura limpia. Todo el codigo importable cuelga de aqui.",
    "app/core": "Nivel 0: primitivas sin dependencias internas.",
    "app/domain": "Nivel 1: entidades y reglas puras del negocio cuantitativo.",
    "app/domain/entities": "Identidad propia que sobrevive al cambio de atributos.",
    "app/domain/value_objects": "La identidad es el contenido.",
    "app/domain/services": "Reglas que no pertenecen a una sola entidad.",
    "app/core/registry": "Catalogo de componentes con espacio de parametros declarado.",
    "app/core/config": "Configuracion: esquema, precedencia y procedencia. Puro.",
    "app/events": "Bus de eventos en proceso. Mecanismo puro, sin I/O.",
    "app/config": "Proveedores de configuracion. Unico lugar que lee ficheros de config.",
    "app/container": "Raiz de composicion: inyeccion, arranque y ciclo de vida.",
    "app/shared": "Nivel 2: puertos (Protocol) entre el dominio y el mundo.",
    # -- casos de uso: un paquete por caso, con contrato explicito.
    #    command.py   la operacion solicitada, como dato
    #    request.py   entrada del caso de uso
    #    response.py  salida del caso de uso
    #    service.py   implementacion del caso de uso
    #    runner.py    orquestacion: resuelve puertos y ejecuta el service
    "app/application": "Casos de uso. Un paquete por caso, nunca un fichero suelto.",
    "app/application/backtest": "Caso de uso: simulacion historica determinista.",
    "app/application/discovery": "Caso de uso: generacion y evaluacion de arquitecturas.",
    "app/application/live": "Caso de uso: ciclo continuo de ejecucion real y paper.",
    "app/application/promotion": "Caso de uso: evaluacion cientifica y paso al zoo.",
    "app/research": "Motor de investigacion determinista.",
    "app/research/data": "Ingesta, normalizacion y validacion de series.",
    "app/research/features": "Indicadores y features. Funciones puras y causales.",
    "app/research/signals": "Features a decisiones por barra, con codigo de razon.",
    "app/research/strategies": "Materializacion de un StrategySpec en bloques ejecutables.",
    "app/research/backtest": "Motor bar a bar.",
    # -- discovery subdividido desde el primer dia. Hoy hay un fichero por
    #    carpeta; en seis meses habra veinte, y entonces reorganizar cuesta.
    "app/discovery": "Generacion, mutacion y recombinacion de arquitecturas.",
    "app/discovery/catalog": "Inventario de bloques disponibles y su espacio de parametros.",
    "app/discovery/graph": "Representacion de una arquitectura como grafo de bloques.",
    "app/discovery/generator": "Construccion de candidatos desde el espacio de busqueda.",
    # -- evolucion: su propio subarbol desde ya. En cuanto entren NSGA-II,
    #    CMA-ES, evolucion diferencial o programacion genetica, cada uno trae
    #    su seleccion y su elitismo. Mezclarlos con `generator/` obligaria a
    #    reorganizar justo cuando hay mas codigo que mover.
    "app/discovery/evolution": "Metaheuristicas poblacionales.",
    "app/discovery/evolution/mutation": "Perturbaciones locales de una arquitectura.",
    "app/discovery/evolution/crossover": "Recombinacion de dos arquitecturas.",
    "app/discovery/evolution/selection": "Torneo, ruleta, dominancia de Pareto.",
    "app/discovery/evolution/elitism": "Conservacion de los mejores entre generaciones.",
    "app/discovery/constraints": "Restricciones duras: que composiciones son legales.",
    "app/discovery/serialization": "Ida y vuelta entre arquitectura y StrategySpec.",
    "app/discovery/evaluation": "Evaluacion de un candidato: coordina backtest y metricas.",
    "app/discovery/scoring": "Puntuacion multicriterio y ranking de candidatos.",
    "app/discovery/pruning": "Poda, deduplicacion y lista negra.",
    # -- gobierno del zoo. Subsistema propio, NO una carpeta de discovery: una
    #    estrategia escrita a mano debe recorrer el mismo camino de promocion
    #    que una generada por la busqueda.
    "app/promotion": "Gobierno del ciclo de vida del zoo.",
    "app/promotion/eligibility": "Puertas de entrada: que candidato puede ni siquiera evaluarse.",
    "app/promotion/ranking": "Orden entre candidatos elegibles, con penalizacion por complejidad.",
    "app/promotion/approval": "Veredicto y constancia de quien aprueba y cuando.",
    "app/promotion/deployment": "Paso a paper y a live, con sus salvaguardas.",
    "app/promotion/archive": "Retiro y conservacion de la evidencia del rechazo.",
    # -- el zoo no es un JSON de parametros: es un catalogo con historia.
    "app/storage/zoo": "Catalogo canonico de estrategias con su linaje completo.",
    "app/storage/artifacts": "Repositorio de artefactos con procedencia obligatoria.",
    "app/optimization": "Busquedas: grid, random, guiada, evolutiva.",
    "app/walkforward": "Particionado en folds, evaluacion y agregacion.",
    "app/validation": "Pruebas estadisticas: permutacion, Monte Carlo, stress.",
    "app/execution": "Maquina de estados de ordenes, validacion y ruteo.",
    "app/portfolio": "Riesgo y dimensionamiento con vision de cartera.",
    "app/broker": "Adaptadores de broker.",
    "app/storage": "Zoo canonico, zoo espejo y repositorio de artefactos.",
    "app/monitoring": "Metricas de runtime, auditoria y health checks.",
    "app/analytics": "Calculo de metricas y reporting.",
    "app/paper": "Motor sobre feed en tiempo real, sin ejecucion real.",
    "app/live": "Motor de ejecucion real.",
    "app/interfaces": "Nivel 6: puntos de entrada.",
    "app/interfaces/cli": "Comandos de terminal.",
    "app/interfaces/api": "Endpoints de monitoreo, opcionales.",
}

#: Directorios que existen pero no son paquetes. Solo se garantiza que existan.
PLAIN_DIRS: tuple[str, ...] = (
    "configs",
    "configs/symbols",
    "configs/strategies",
    "configs/walkforward",
    "data",
    "artifacts",
    "experiments",
    "docs",
    "scripts",
    "tests",
    "reports",
)


@dataclass(frozen=True, slots=True)
class Move:
    source: str
    target: str
    reason: str


#: Movimientos con `git mv`. El destino ya tiene su paquete creado antes.
MOVES: tuple[Move, ...] = (
    Move("app/domain/bars.py", "app/domain/entities/bars.py", "serie con identidad propia"),
    Move("app/domain/order.py", "app/domain/entities/order.py", "identidad estable entre estados"),
    Move("app/domain/trade.py", "app/domain/entities/trade.py", "Position y Trade son entidades"),
    Move(
        "app/domain/instrument.py",
        "app/domain/value_objects/instrument.py",
        "su identidad es su contenido",
    ),
    Move(
        "app/domain/signal.py",
        "app/domain/value_objects/signal.py",
        "salida vectorizada sin identidad propia",
    ),
    Move(
        "app/domain/metrics.py",
        "app/domain/value_objects/metrics.py",
        "resumen inmutable de una serie de trades",
    ),
    Move(
        "app/domain/strategy_spec.py",
        "app/domain/value_objects/strategy_spec.py",
        "su id ES el hash de su composicion",
    ),
    # Un proveedor por fichero. `providers.py` se convirtio en cinco clases con
    # cinco mecanismos distintos de lectura; dividirlo ahora cuesta un git mv y
    # deja sitio para los que faltan (parquet, http, secretos).
    Move(
        "app/config/providers.py",
        "app/config/providers/toml.py",
        "TOML es el formato oficial; el resto se extrae a hermanos suyos",
    ),
)

#: Modulos que hay que extraer de un fichero existente tras moverlo. El script
#: NO los crea: solo los declara para que el plan sea explicito y quede
#: constancia de lo que falta. Crearlos vacios violaria "cada archivo tiene un
#: proposito".
PENDING_SPLITS: tuple[tuple[str, str], ...] = (
    ("app/config/providers/yaml.py", "YamlFileProvider, proveedor opcional"),
    ("app/config/providers/env.py", "EnvironmentProvider y la traduccion QP_*"),
    ("app/config/providers/cli.py", "CommandLineProvider y MappingProvider"),
    ("app/config/providers/base.py", "el Protocol ConfigProvider y `coerce`"),
)

#: Rutas a eliminar del arbol y del indice de git.
REMOVALS: tuple[tuple[str, str], ...] = (
    ("core", "arbol intermedio abandonado; su contenido vive en app/core"),
    ("domain", "arbol intermedio abandonado; su contenido vive en app/domain"),
    ("app/core/errors.py", "renombrado a app/core/exceptions.py"),
    ("app/infrastructure", "disuelto en app/broker, app/storage y app/monitoring"),
    (
        "app/shared/registry.py",
        "el registro esta por debajo de features y signals: app/core/registry/",
    ),
    (
        "app/core/registry.py",
        "sustituido por el paquete app/core/registry/; un modulo y un paquete "
        "con el mismo nombre en el mismo directorio es una ambiguedad de import",
    ),
)

#: Basura que nunca debe versionarse ni sobrevivir a la consolidacion.
DEAD_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.old",
    "*.tmp",
    "*.bak",
    "*.orig",
    "*.rej",
    "legacy",
    "unused",
)

# ---------------------------------------------------------------------------
# 2. Reescritura de imports
# ---------------------------------------------------------------------------

#: El ORDEN IMPORTA. Primero se normaliza el prefijo de los arboles viejos a
#: `app.*`, y solo despues se aplican los renombrados concretos. Si se hiciera
#: al reves, `from core.errors import X` quedaria como `from app.core.errors`,
#: que ya no existe.
#:
#: Los patrones usan `\b` al final para no reescribir dos veces: `app\.domain\.bars\b`
#: no casa con `app.domain.entities.bars`, asi que reejecutar es inofensivo.
IMPORT_REWRITES: tuple[tuple[str, str], ...] = (
    # -- fase A: arboles intermedios -> app.*
    (r"(?m)^(\s*)from\s+core\.", r"\1from app.core."),
    (r"(?m)^(\s*)from\s+domain\.", r"\1from app.domain."),
    (r"(?m)^(\s*)import\s+core\.", r"\1import app.core."),
    (r"(?m)^(\s*)import\s+domain\.", r"\1import app.domain."),
    (r"(?m)^(\s*)from\s+shared\.", r"\1from app.shared."),
    # -- fase B: renombrado de modulos
    (r"\bapp\.core\.errors\b", "app.core.exceptions"),
    (r"\bapp\.shared\.registry\b", "app.core.registry"),
    # -- fase C: reubicacion dentro del dominio
    (r"\bapp\.domain\.bars\b", "app.domain.entities.bars"),
    (r"\bapp\.domain\.order\b", "app.domain.entities.order"),
    (r"\bapp\.domain\.trade\b", "app.domain.entities.trade"),
    (r"\bapp\.domain\.instrument\b", "app.domain.value_objects.instrument"),
    (r"\bapp\.domain\.signal\b", "app.domain.value_objects.signal"),
    (r"\bapp\.domain\.metrics\b", "app.domain.value_objects.metrics"),
    (r"\bapp\.domain\.strategy_spec\b", "app.domain.value_objects.strategy_spec"),
    # -- fase D: motores que bajaron dentro de app/
    (r"(?m)^(\s*)from\s+(features|signals|strategies|backtest)\.", r"\1from app.research.\2."),
    (r"(?m)^(\s*)from\s+risk\.", r"\1from app.portfolio."),
    (
        r"(?m)^(\s*)from\s+(discovery|optimization|walkforward|validation|execution"
        r"|broker|storage|monitoring|analytics|paper|live)\.",
        r"\1from app.\2.",
    ),
)

#: Prohibiciones que se comprueban al final. Si alguna casa, la consolidacion
#: no esta completa y el script termina en rojo.
FORBIDDEN_AFTER: tuple[tuple[str, str], ...] = (
    (r"(?m)^\s*from\s+core\.", "queda un import del arbol `core/`"),
    (r"(?m)^\s*from\s+domain\.", "queda un import del arbol `domain/`"),
    (r"\bapp\.core\.errors\b", "queda una referencia a `app.core.errors`"),
    (r"\bapp\.shared\.registry\b", "queda una referencia a `app.shared.registry`"),
)


# ---------------------------------------------------------------------------
# 3. Utilidades
# ---------------------------------------------------------------------------

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def say(message: str, *, level: str = "") -> None:
    prefix = {"ok": f"{GREEN}  ok{OFF}", "err": f"{RED} err{OFF}", "skip": f"{DIM}skip{OFF}"}
    print(f"{prefix.get(level, '    ')} {message}")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=check
    )


def is_git_repo() -> bool:
    return (ROOT / ".git").exists()


def python_files() -> list[Path]:
    """Todos los `.py` versionables del arbol, excluyendo entornos y caches.

    Este mismo fichero queda fuera. No es una comodidad: es el unico modulo del
    repositorio que contiene los nombres antiguos como DATO -las tablas
    `REMOVALS`, `IMPORT_REWRITES` y `FORBIDDEN_AFTER` los declaran-. Incluirlo
    tendria dos efectos, ambos incorrectos: la reescritura corromperia sus
    propias reglas -`(app.core.errors -> app.core.exceptions)` pasaria a ser
    `(app.core.exceptions -> app.core.exceptions)`, dejando el script sin efecto
    en cuanto se ejecutase dos veces- y la comprobacion final lo denunciaria a
    si mismo para siempre, de modo que la consolidacion nunca podria declararse
    completa y el tag `architecture-baseline` nunca llegaria a crearse.

    El script es la herramienta, no el arbol que gobierna.
    """
    excluded = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache"}
    this_file = Path(__file__).resolve()
    return [
        p
        for p in ROOT.rglob("*.py")
        if not any(part in excluded for part in p.relative_to(ROOT).parts)
        and p.resolve() != this_file
    ]


# ---------------------------------------------------------------------------
# 4. Pasos
# ---------------------------------------------------------------------------


def step_scaffold(apply: bool) -> None:
    print(f"\n{BOLD}[1/7] Arbol congelado{OFF}")
    for rel, purpose in PACKAGES.items():
        directory = ROOT / rel
        init = directory / "__init__.py"
        if init.exists():
            say(f"{rel}/", level="skip")
            continue
        say(f"crear {rel}/__init__.py  {DIM}{purpose}{OFF}", level="ok")
        if apply:
            directory.mkdir(parents=True, exist_ok=True)
            init.write_text(f'"""{purpose}"""\n', encoding="utf-8")

    for rel in PLAIN_DIRS:
        directory = ROOT / rel
        keep = directory / ".gitkeep"
        if directory.exists():
            continue
        say(f"crear {rel}/", level="ok")
        if apply:
            directory.mkdir(parents=True, exist_ok=True)
            keep.touch()


def step_moves(apply: bool) -> None:
    print(f"\n{BOLD}[2/7] git mv (se conserva el historial){OFF}")
    for move in MOVES:
        source, target = ROOT / move.source, ROOT / move.target
        if not source.exists():
            say(f"{move.source}  (ya movido)", level="skip")
            continue
        if target.exists():
            # El destino ya se escribio en una pasada anterior. Se elimina el
            # origen del indice; git detecta el renombrado por similitud de
            # contenido, asi que `git log --follow` sigue funcionando.
            say(f"rm  {move.source}  {DIM}destino ya presente{OFF}", level="ok")
            if apply:
                git("rm", "-q", "--", move.source, check=False)
                source.unlink(missing_ok=True)
            continue
        say(f"mv  {move.source} -> {move.target}  {DIM}{move.reason}{OFF}", level="ok")
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            result = git("mv", "--", move.source, move.target, check=False)
            if result.returncode != 0:
                # Fichero aun no versionado: se mueve en disco y se anade.
                shutil.move(str(source), str(target))
                git("add", "--", move.target, check=False)

    if PENDING_SPLITS:
        print(f"\n{DIM}    Modulos por extraer a mano (no se crean vacios):{OFF}")
        for path, what in PENDING_SPLITS:
            say(f"{path}  {DIM}{what}{OFF}", level="skip")


def step_removals(apply: bool) -> None:
    print(f"\n{BOLD}[3/7] Duplicados y arboles muertos{OFF}")
    for rel, reason in REMOVALS:
        path = ROOT / rel
        if not path.exists():
            say(f"{rel}  (ausente)", level="skip")
            continue
        say(f"rm  {rel}  {DIM}{reason}{OFF}", level="ok")
        if apply:
            git("rm", "-r", "-q", "--", rel, check=False)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def step_purge(apply: bool) -> None:
    print(f"\n{BOLD}[4/7] Codigo muerto{OFF}")
    removed = 0
    for pattern in DEAD_PATTERNS:
        for path in sorted(ROOT.rglob(pattern)):
            if ".git" in path.parts or ".venv" in path.parts:
                continue
            removed += 1
            if apply:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
    say(f"{removed} rutas coincidentes con {len(DEAD_PATTERNS)} patrones", level="ok")


def step_imports(apply: bool) -> int:
    print(f"\n{BOLD}[5/7] Imports{OFF}")
    touched = 0
    for path in python_files():
        original = path.read_text(encoding="utf-8")
        updated = original
        for pattern, replacement in IMPORT_REWRITES:
            updated = re.sub(pattern, replacement, updated)
        if updated == original:
            continue
        touched += 1
        say(str(path.relative_to(ROOT)), level="ok")
        if apply:
            path.write_text(updated, encoding="utf-8")
    say(f"{touched} ficheros con imports reescritos")

    violations = 0
    for path in python_files():
        text = path.read_text(encoding="utf-8")
        for pattern, description in FORBIDDEN_AFTER:
            if re.search(pattern, text):
                violations += 1
                say(f"{path.relative_to(ROOT)}: {description}", level="err")
    return violations


def step_verify() -> int:
    print(f"\n{BOLD}[7/7] Verificacion{OFF}")
    failures = 0

    compile_result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_result.returncode == 0:
        say("python -m compileall app", level="ok")
    else:
        failures += 1
        say("python -m compileall app", level="err")
        print(compile_result.stdout or compile_result.stderr)

    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 5 = "no se recogio ningun test". Es aceptable antes de la fase 10, pero
    # se informa: un arbol sin tests no esta verde, esta vacio.
    if pytest_result.returncode == 0:
        say("pytest", level="ok")
    elif pytest_result.returncode == 5:
        say("pytest: no hay tests todavia", level="skip")
    else:
        failures += 1
        say("pytest", level="err")
        print(pytest_result.stdout[-4000:])

    return failures


# ---------------------------------------------------------------------------
# 4b. Evidencia
# ---------------------------------------------------------------------------

REPORTS = ROOT / "reports"
MATRIX_FILE = ROOT / "configs" / "architecture.toml"


def _app_modules() -> list[Path]:
    return sorted(p for p in (ROOT / "app").rglob("*.py") if "__pycache__" not in p.parts)


def _dotted(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve(dotted: str) -> Path | None:
    base = ROOT / Path(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _build_graph() -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Grafo de imports internos y tabla de simbolos exportados por modulo."""
    import ast

    graph: dict[str, set[str]] = {}
    symbols: dict[str, list[str]] = {}
    for path in _app_modules():
        name = _dotted(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        edges: set[str] = set()
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module and module.startswith("app."):
                resolved = _resolve(module)
                if resolved is not None:
                    target = _dotted(resolved)
                    if target != name:
                        edges.add(target)
        graph[name] = edges
        symbols[name] = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]
    return graph, symbols


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Todos los ciclos elementales alcanzables, sin repetir rotaciones."""
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    visited: set[str] = set()

    def walk(node: str, stack: list[str], on_stack: set[str]) -> None:
        visited.add(node)
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.get(node, ())):
            if neighbour in on_stack:
                cycle = [*stack[stack.index(neighbour) :], neighbour]
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle)
            elif neighbour not in visited:
                walk(neighbour, stack, on_stack)
        stack.pop()
        on_stack.discard(node)

    for node in sorted(graph):
        if node not in visited:
            walk(node, [], set())
    return cycles


def _render_svg(levels: dict[str, int], edges: set[tuple[str, str]]) -> str:
    """Grafo de capas en SVG plano, sin graphviz.

    Se genera a mano a proposito: anadir graphviz obligaria a instalar un
    binario del sistema para poder ver un diagrama, y un reporte que no se
    puede generar en CI no se genera nunca.
    """
    by_level: dict[int, list[str]] = {}
    for package, level in sorted(levels.items(), key=lambda kv: (kv[1], kv[0])):
        by_level.setdefault(level, []).append(package)

    box_w, box_h, gap_x, gap_y, margin = 168, 40, 22, 76, 28
    widest = max((len(v) for v in by_level.values()), default=1)
    width = margin * 2 + widest * box_w + (widest - 1) * gap_x
    height = margin * 2 + len(by_level) * box_h + (len(by_level) - 1) * gap_y

    centre: dict[str, tuple[float, float]] = {}
    for row, (_level, packages) in enumerate(sorted(by_level.items())):
        span = len(packages) * box_w + (len(packages) - 1) * gap_x
        x0 = (width - span) / 2
        y = margin + row * (box_h + gap_y)
        for column, package in enumerate(packages):
            x = x0 + column * (box_w + gap_x)
            centre[package] = (x + box_w / 2, y + box_h / 2)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="ui-monospace,monospace" font-size="11">',
        '<defs><marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#94a3b8"/>'
        "</marker></defs>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for source, target in sorted(edges):
        if source not in centre or target not in centre:
            continue
        x1, y1 = centre[source]
        x2, y2 = centre[target]
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1 - box_h / 2:.0f}" x2="{x2:.0f}" '
            f'y2="{y2 + box_h / 2:.0f}" stroke="#cbd5e1" stroke-width="1" '
            'marker-end="url(#a)"/>'
        )
    for package, (cx, cy) in sorted(centre.items()):
        parts.append(
            f'<rect x="{cx - box_w / 2:.0f}" y="{cy - box_h / 2:.0f}" width="{box_w}" '
            f'height="{box_h}" rx="5" fill="#f8fafc" stroke="#475569" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{cx:.0f}" y="{cy + 4:.0f}" text-anchor="middle" fill="#0f172a">'
            f"{package.removeprefix('app.')}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def step_reports(apply: bool) -> None:
    """Genera la evidencia de la consolidacion.

    Cada corrida deja constancia verificable. Sin evidencia, "el arbol esta
    limpio" es una afirmacion que hay que creer; con ella es una que se puede
    comprobar y, sobre todo, comparar contra la corrida anterior.
    """
    import tomllib

    print(f"\n{BOLD}[6/7] Evidencia{OFF}")
    graph, symbols = _build_graph()
    matrix = tomllib.loads(MATRIX_FILE.read_text(encoding="utf-8")) if MATRIX_FILE.exists() else {}
    # `architecture.toml` declara los paquetes con nombre corto; aqui se
    # prefijan para poder cruzarlos con los modulos reales del arbol.
    declared: dict[str, dict[str, object]] = matrix.get("packages", {})
    ranks: dict[str, int] = {k: int(v) for k, v in matrix.get("layer_order", {}).items()}
    levels = {
        f"app.{name}": ranks.get(str(spec.get("layer", "")), 0)
        for name, spec in declared.items()
    }
    allowed_by: dict[str, set[str]] = {
        f"app.{name}": {f"app.{dep}" for dep in spec.get("depends", [])}  # type: ignore[union-attr]
        for name, spec in declared.items()
    }

    def package_of(dotted: str) -> str:
        parts = dotted.split(".")
        for depth in range(min(len(parts), 3), 0, -1):
            candidate = ".".join(parts[:depth])
            if candidate in levels:
                return candidate
        return "app"

    cycles = _find_cycles(graph)
    imported = {target for targets in graph.values() for target in targets}
    orphans = sorted(
        name
        for name in graph
        if name not in imported and not name.endswith("__init__") and name != "app"
    )
    duplicated: dict[str, list[str]] = {}
    for module, names in symbols.items():
        for symbol in names:
            duplicated.setdefault(symbol, []).append(module)
    duplicated = {s: m for s, m in sorted(duplicated.items()) if len(m) > 1}

    package_edges = {
        (package_of(source), package_of(target))
        for source, targets in graph.items()
        for target in targets
        if package_of(source) != package_of(target)
    }
    violations = [
        f"{source} -> {target}"
        for source, target in sorted(package_edges)
        if target not in allowed_by.get(source, set())
    ]

    files = {
        "import_cycles.txt": (
            "\n".join(" -> ".join(c) for c in cycles) or "sin ciclos\n"
        ),
        "orphan_modules.txt": (
            "\n".join(orphans) or "sin modulos huerfanos\n"
        ),
        "duplicated_symbols.txt": (
            "\n".join(f"{s}: {', '.join(m)}" for s, m in duplicated.items())
            or "sin simbolos duplicados\n"
        ),
        "dependency_graph.svg": _render_svg(levels, package_edges),
        "architecture_report.md": _report_markdown(
            graph, levels, package_edges, violations, cycles, orphans, duplicated
        ),
    }

    for filename, content in files.items():
        say(f"reports/{filename}", level="ok")
        if apply:
            REPORTS.mkdir(parents=True, exist_ok=True)
            (REPORTS / filename).write_text(content, encoding="utf-8")

    if violations:
        for violation in violations:
            say(f"matriz: {violation}", level="err")


def _report_markdown(
    graph: dict[str, set[str]],
    levels: dict[str, int],
    package_edges: set[tuple[str, str]],
    violations: list[str],
    cycles: list[list[str]],
    orphans: list[str],
    duplicated: dict[str, list[str]],
) -> str:
    from datetime import UTC, datetime

    rows = "\n".join(
        f"| {package} | {levels.get(package, '-')} | "
        f"{sum(1 for m in graph if m.startswith(package))} | "
        f"{len({t for s, t in package_edges if s == package})} |"
        for package in sorted(levels)
    )
    section = lambda title, items: (  # noqa: E731
        f"\n## {title}\n\n" + ("\n".join(f"- `{i}`" for i in items) if items else "_ninguno_\n")
    )
    return (
        "# Reporte de consolidacion arquitectonica\n\n"
        f"Generado: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n"
        f"- Modulos analizados: **{len(graph)}**\n"
        f"- Aristas entre paquetes: **{len(package_edges)}**\n"
        f"- Violaciones de la matriz: **{len(violations)}**\n"
        f"- Ciclos de importacion: **{len(cycles)}**\n"
        f"- Modulos huerfanos: **{len(orphans)}**\n"
        f"- Simbolos duplicados: **{len(duplicated)}**\n\n"
        "## Paquetes\n\n"
        "| Paquete | Nivel | Modulos | Dependencias |\n|---|---|---|---|\n" + rows + "\n"
        + section("Violaciones de la matriz", violations)
        + section("Ciclos", [" -> ".join(c) for c in cycles])
        + section("Huerfanos", orphans)
        + section("Simbolos duplicados", [f"{s}: {', '.join(m)}" for s, m in duplicated.items()])
        + "\n---\n\nUn modulo huerfano no es necesariamente un error: los puntos de "
        "entrada y los modulos que solo se importan dinamicamente aparecen aqui. "
        "Lo que si es una senal es que crezcan entre dos consolidaciones.\n"
    )


# ---------------------------------------------------------------------------
# 5. Entrada
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 2.5 - consolidacion arquitectonica")
    parser.add_argument("--apply", action="store_true", help="ejecuta los cambios")
    parser.add_argument("--tag", action="store_true", help="crea architecture-baseline si esta verde")
    args = parser.parse_args()

    if not is_git_repo():
        say("No hay repositorio git. `git init` primero: sin el no hay `git mv`.", level="err")
        return 1

    mode = "APLICANDO" if args.apply else "PLAN (nada se modifica)"
    print(f"{BOLD}Fase 2.5 - Consolidacion arquitectonica  [{mode}]{OFF}")
    print(f"raiz: {ROOT}")

    if args.apply:
        dirty = git("status", "--porcelain").stdout.strip()
        if dirty:
            say(
                f"El arbol tiene {len(dirty.splitlines())} cambios sin confirmar. "
                "Haz commit antes: esta operacion debe poder revertirse de una pieza.",
                level="err",
            )
            return 1

    step_scaffold(args.apply)
    step_moves(args.apply)
    step_removals(args.apply)
    step_purge(args.apply)
    violations = step_imports(args.apply)
    step_reports(args.apply)

    if not args.apply:
        print(f"\n{DIM}Plan generado. Ejecuta con --apply para materializarlo.{OFF}")
        return 0

    failures = step_verify() + violations

    print()
    if failures:
        say(f"Consolidacion incompleta: {failures} problemas. No se etiqueta.", level="err")
        return 1

    say("Arbol consolidado y verde.", level="ok")
    if args.tag:
        git("add", "-A")
        git("commit", "-m", "fase 2.5: consolidacion arquitectonica", check=False)
        git("tag", "-a", "architecture-baseline", "-m", "Arquitectura base congelada")
        say("tag architecture-baseline creado", level="ok")
    else:
        say("Revisa el diff y etiqueta con --tag cuando estes conforme.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
