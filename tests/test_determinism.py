"""Determinismo de la plataforma. Es P1, y P1 se comprueba ejecutando.

La Definition of Done de `configs/delivery.toml` marca `deterministic` y
`fingerprint_stable` como criterios bloqueantes, y dice como se verifican:
doble ejecucion con la misma semilla, comparacion bit a bit. Este fichero es esa
verificacion.

Se comprueba ejecutando y no razonando sobre el codigo, que es la parte que
suele omitirse. El determinismo se rompe por caminos que nadie anticipa -un
`set` iterado, un diccionario ordenado por hash, una suma en paralelo- y ninguno
de ellos es visible leyendo. Lo unico que los detecta es correr dos veces y
comparar.

La comparacion incluye un **proceso separado**. Dentro del mismo interprete, el
`PYTHONHASHSEED` es constante y una dependencia del orden de hash pasaria
inadvertida; entre procesos distintos, no.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.container.bootstrap import build_platform, load_configuration
from app.shared.ports import ClockPort

ROOT = Path(__file__).resolve().parent.parent

#: Modo con el reloj detenido. Comparar en `research` fallaria por el motivo
#: equivocado: alli el reloj es real a proposito y cada corrida marca su instante.
DETERMINISTIC_MODE = "ci"


@pytest.mark.unit
def test_config_fingerprint_is_stable_within_a_process() -> None:
    """La misma entrada produce la misma huella, siempre."""
    fingerprints = {
        str(load_configuration(ROOT, mode=DETERMINISTIC_MODE).fingerprint) for _ in range(5)
    }
    assert len(fingerprints) == 1, (
        f"La huella de configuracion cambia entre resoluciones identicas: {fingerprints}. "
        "Sin huella estable no se puede afirmar que dos corridas usaron la misma "
        "configuracion, y toda comparacion posterior deja de significar nada."
    )


@pytest.mark.unit
def test_config_fingerprint_survives_a_new_process() -> None:
    """La huella no depende de la aleatorizacion de hash del interprete.

    Es el caso que una comprobacion dentro del mismo proceso no puede ver: si la
    huella se calculara recorriendo un `set` o un `dict` sin ordenar, seria
    estable dentro de una ejecucion y distinta en la siguiente.
    """
    first = _fingerprint_from_subprocess()
    second = _fingerprint_from_subprocess()
    assert first == second, (
        f"La huella cambia entre procesos: {first!r} != {second!r}. "
        "Algo en el calculo depende del orden de hash del interprete."
    )


@pytest.mark.unit
def test_overrides_change_the_fingerprint() -> None:
    """Una configuracion distinta produce una huella distinta.

    El reciproco del test anterior, y el que de verdad da valor a la huella. Una
    funcion que devolviera una constante pasaria los dos primeros tests y no
    detectaria ningun cambio de configuracion.
    """
    base = load_configuration(ROOT, mode=DETERMINISTIC_MODE)
    changed = load_configuration(
        ROOT, mode=DETERMINISTIC_MODE, overrides={"runtime.frozen_clock_ns": 42}
    )
    assert str(base.fingerprint) != str(changed.fingerprint), (
        "Dos configuraciones distintas producen la misma huella. La huella no "
        "distingue corridas y no sirve como identidad."
    )


@pytest.mark.unit
def test_platform_composes_identically_twice() -> None:
    """Dos composiciones producen el mismo grafo y el mismo orden de arranque.

    El orden de arranque importa tanto como el contenido: dos ejecuciones que
    inicializasen en distinto orden emitirian secuencias de eventos diferentes
    con la misma configuracion, y eso rompe P1 aunque el resultado numerico
    coincida.
    """
    first = build_platform(ROOT, mode=DETERMINISTIC_MODE).describe()
    second = build_platform(ROOT, mode=DETERMINISTIC_MODE).describe()
    assert first == second, "La composicion de la plataforma no es reproducible."
    assert first["cycle"] is None, f"El grafo de dependencias tiene un ciclo: {first['cycle']}"


@pytest.mark.unit
def test_frozen_clock_does_not_advance() -> None:
    """En modo determinista el reloj no avanza entre dos lecturas.

    Sin esto, cualquier regla horaria daria un resultado distinto en cada
    ejecucion y las pruebas de logica de sesion exigirian esperar al reloj real.
    """
    clock = build_platform(ROOT, mode=DETERMINISTIC_MODE).resolve(ClockPort)
    readings = {clock.now_ns() for _ in range(3)}
    assert len(readings) == 1, f"El reloj de un modo determinista avanzo: {readings}"


def _fingerprint_from_subprocess() -> str:
    """Huella calculada por un interprete recien arrancado."""
    script = (
        f"import sys;sys.path.insert(0,{str(ROOT)!r});"
        "from pathlib import Path;"
        "from app.container.bootstrap import load_configuration;"
        f"print(load_configuration(Path({str(ROOT)!r}), mode={DETERMINISTIC_MODE!r}).fingerprint)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return completed.stdout.strip()


@pytest.mark.unit
def test_preflight_output_is_reproducible() -> None:
    """`qp preflight --json` produce el mismo informe en dos procesos.

    Es la comprobacion de extremo a extremo: si algo de la cadena -resolucion,
    composicion, inventario de plugins, orden de los hallazgos- dependiera del
    orden de un `set`, aqui se veria.
    """
    first, second = _preflight_payload(), _preflight_payload()
    assert first == second, "Dos ejecuciones identicas de preflight producen informes distintos."


def _preflight_payload() -> str:
    """Informe de preflight sin el bloque de entorno.

    `environment` se excluye a proposito: registra version de Python y de
    sistema operativo, que son diagnostico y NO forman parte de la identidad de
    una corrida. Si entraran, actualizar un parche haria imposible comparar con
    cualquier corrida anterior aunque el resultado fuera identico bit a bit.
    """
    script = (
        f"import sys;sys.path.insert(0,{str(ROOT)!r});"
        "from app.interfaces.cli import main;"
        f"main(['preflight','--mode',{DETERMINISTIC_MODE!r},'--json'])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT
    )
    payload = json.loads(completed.stdout)
    payload.pop("environment", None)
    return json.dumps(payload, sort_keys=True)


__all__: list[str] = []
