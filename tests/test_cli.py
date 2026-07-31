"""Comportamiento de los puntos de entrada de la CLI.

Cubre el hueco por el que un defecto llego hasta el criterio de aceptacion de la
Fase 3 sin que nada lo detectara: que los comandos ESCRIBAN sin romperse.

`qp doctor` terminaba con `UnicodeEncodeError` y codigo 1 en la consola por
defecto de Windows -cp1252 no puede codificar el `U+2713` con el que se marca
cada comprobacion-, mientras `qp doctor --json` salia 0. Ningun test ejercitaba
la ruta de texto, asi que la suite estaba verde y `BUILD.md` daba por cumplido un
criterio que fallaba en el sistema operativo del propio proyecto. Un gate que
pasa solo en uno de sus dos formatos de salida no es un gate.

Se invoca `main.py` y no el ejecutable instalado porque su docstring garantiza
que ambos recorren el mismo camino, y `main.py` existe en cualquier clon sin
depender de que el paquete este instalado.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.interfaces.cli import COMMANDS

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "main.py"

#: Invocacion -> codigo de salida esperado.
#:
#: `status` en texto devuelve 1 a proposito: informa de que la plataforma no esta
#: lista para Discovery, y hoy esa es la respuesta correcta. Que en `--json`
#: devuelva 0 para el MISMO estado es una incoherencia real, registrada aparte y
#: pendiente de su propio sprint. Se fija aqui el comportamiento actual, no el
#: deseable, para que el dia que se unifique el cambio sea visible en este test
#: en lugar de pasar inadvertido.
EXPECTED_EXIT: dict[str, int] = {
    "--help": 0,
    "version": 0,
    "doctor": 0,
    "doctor --json": 0,
    "preflight": 0,
    "preflight --json": 0,
    "status": 1,
    "status --json": 0,
    "config show": 0,
    "config validate": 0,
    "plugins list": 0,
    "plugins validate": 0,
}


def _run(invocation: str, *, encoding: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta una invocacion de la CLI con la codificacion de salida forzada."""
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    return subprocess.run(
        [sys.executable, str(ENTRY), *invocation.split()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        check=False,
    )


@pytest.mark.integration
@pytest.mark.parametrize("invocation", sorted(EXPECTED_EXIT), ids=sorted(EXPECTED_EXIT))
def test_command_exits_as_documented_under_a_narrow_codec(invocation: str) -> None:
    """Ningun comando revienta al escribir en una consola que no admite UTF-8.

    `cp1252` no es un caso exotico: es la codificacion por defecto de la consola
    de Windows, el sistema donde se desarrolla este proyecto. Se fuerza de forma
    explicita para que el test detecte la regresion tambien cuando la suite se
    ejecute en un CI cuya consola si admita UTF-8.

    Se comprueba el codigo de salida y no el texto: la salida es presentacion y
    cambiara, mientras que "0 correcto, 1 fallo del comando, 2 uso incorrecto" es
    el contrato que `app/interfaces/cli/__init__.py` documenta.
    """
    result = _run(invocation, encoding="cp1252")
    assert result.returncode == EXPECTED_EXIT[invocation], (
        f"`qp {invocation}` devolvio {result.returncode}, se esperaba "
        f"{EXPECTED_EXIT[invocation]}.\nstderr:\n{result.stderr}"
    )
    assert "UnicodeEncodeError" not in result.stderr, (
        f"`qp {invocation}` no pudo codificar su salida en cp1252:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, (
        f"`qp {invocation}` termino con una excepcion no capturada:\n{result.stderr}"
    )


@pytest.mark.integration
def test_doctor_reports_the_platform_as_ready() -> None:
    """`qp doctor` cumple el criterio de aceptacion de la Fase 3 en modo texto.

    `BUILD.md` lo fija literalmente: `qp doctor` -> `Platform READY`. Se verifica
    sobre la salida de TEXTO porque era la que fallaba; la de `--json` ya
    funcionaba y por eso el defecto sobrevivio.
    """
    result = _run("doctor", encoding="cp1252")
    assert result.returncode == 0, result.stderr
    assert "Platform READY" in result.stdout, result.stdout


@pytest.mark.unit
def test_utf8_setup_tolerates_a_stream_that_cannot_be_reconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La preparacion de la salida no lanza si el flujo no admite reconfigurarse.

    Es la promesa que hace `_use_utf8_output`: si el entorno no lo permite, se
    continua con el flujo existente. El caso realista no es una consola hostil
    sino un `stdout` sustituido -un doble de test, una tuberia envuelta-, y
    `io.StringIO` lo reproduce: acepta `str` y no tiene `reconfigure`.

    Se prueba la funcion directamente y no a traves de `main` porque la rama que
    interesa es inalcanzable desde fuera: en un proceso real `sys.stdout` siempre
    es reconfigurable.

    El import es local a proposito. Es el unico test de este fichero acoplado a un
    nombre privado, y a nivel de modulo el acoplamiento se propagaria a los tests
    de subproceso: si el helper se renombra, la recoleccion fallaria y las
    comprobaciones de COMPORTAMIENTO -que son la cobertura que importa- dejarian
    de ejecutarse sin haber dejado de ser validas.
    """
    from app.interfaces.cli import _use_utf8_output

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    _use_utf8_output()

    sys.stdout.write("✓ sigue aceptando texto no ASCII")


@pytest.mark.unit
def test_every_declared_command_has_an_expected_exit_code() -> None:
    """La matriz de arriba cubre todos los comandos declarados.

    Sin esto, anadir un comando a `COMMANDS` en una fase posterior lo dejaria sin
    verificar y el hueco se reabriria en silencio, que es exactamente como se
    llego al defecto que este fichero cubre.
    """
    covered = {invocation.split()[0] for invocation in EXPECTED_EXIT}
    missing = sorted(set(COMMANDS) - covered)
    assert not missing, (
        f"Comandos declarados en COMMANDS y sin codigo de salida esperado: {missing}"
    )
