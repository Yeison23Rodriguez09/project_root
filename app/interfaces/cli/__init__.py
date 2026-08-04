"""Comandos de terminal.

Traducen una invocacion humana en una llamada a un caso de uso. No contienen
logica: una interfaz que decide algo habra que duplicarla en cuanto aparezca el
segundo punto de entrada.

El despacho es minimo y **perezoso**: cada comando se importa cuando se invoca.
El motivo es concreto: `qp doctor` debe funcionar en un sistema que no se puede
construir, y si este modulo importara los runners al cargarse, fallaria justo en
los casos donde mas se necesita.

Grupo de plataforma (fase 3). El resto de grupos -backtest, discover,
walkforward, validation, promotion, zoo, paper, live- se anaden en sus fases y
cada uno declara aqui su despacho.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

__version__ = "0.6.0"

#: Comando -> (modulo, descripcion). El modulo se importa solo al invocarse.
#:
#: Convencion sin excepciones: el comando `qp X` vive en `X_cmd.py`. El sufijo
#: no es decorativo. Tres de los cinco nombres colisionan con algo del arbol
#: -`preflight` es tambien el motor de comprobacion en `app.container`, `config`
#: es el paquete de proveedores y `plugins` es el contrato-, y un arbol que
#: prohibe los imports relativos no puede permitirse dos modulos con el mismo
#: nombre corto. Aplicarlo solo a los que chocan obligaria a saber cuales chocan.
COMMANDS: dict[str, tuple[str, str]] = {
    "backtest": ("app.interfaces.cli.backtest_cmd", "simula una estrategia sobre una serie"),
    "doctor": ("app.interfaces.cli.doctor_cmd", "comprueba que la plataforma este bien construida"),
    "download": ("app.interfaces.cli.download_cmd", "descarga un historico y lo registra"),
    "preflight": ("app.interfaces.cli.preflight_cmd", "valida el despliegue antes de operar"),
    "status": ("app.interfaces.cli.status_cmd", "estado calculado de la plataforma"),
    "config": ("app.interfaces.cli.config_cmd", "muestra y valida la configuracion efectiva"),
    "plugins": ("app.interfaces.cli.plugins_cmd", "inventario y validacion de plugins"),
    "version": ("", "version de la plataforma"),
}


def _use_utf8_output() -> None:
    """Reconfigura la salida a UTF-8 antes de escribir un solo caracter.

    Los comandos de plataforma marcan cada comprobacion con `✓`, `✗` y `·`. La
    consola por defecto de Windows es cp1252 y no puede codificar `U+2713`, de
    modo que `qp doctor` -el criterio de aceptacion literal de la Fase 3-
    terminaba con `UnicodeEncodeError` y codigo 1 en el sistema operativo del
    propio proyecto, mientras `qp doctor --json` funcionaba. El gate mentia.

    Se hace aqui, una sola vez, en el unico punto de entrada: cualquier comando
    que se anada en fases posteriores queda cubierto sin acordarse de nada. La
    alternativa -un juego de mensajes ASCII en paralelo- obligaria a mantener dos
    redacciones de la misma salida y a decidir en cada sitio cual usar, que es la
    duplicacion que este cambio existe para evitar.

    Si el entorno no lo permite se continua con el flujo existente y sin lanzar.
    El caso realista no es una consola hostil sino un `stdout` sustituido -un
    doble de test, una tuberia envuelta-, y esos flujos aceptan `str` sin imponer
    codificacion, asi que seguir es correcto y no un riesgo asumido.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # `io.UnsupportedOperation` hereda de las dos: cubre el flujo cerrado
            # y el que no admite reconfiguracion. No se captura `Exception`
            # porque un fallo distinto aqui no seria de codificacion y esconderlo
            # convertiria un error de programacion en una salida silenciosa.
            continue


def _usage() -> str:
    lines = ["", "qp - QuantPlatform", "", "Comandos:"]
    lines += [f"  {name:<12} {help_}" for name, (_m, help_) in sorted(COMMANDS.items())]
    lines += ["", "Usa 'qp <comando> --help' para el detalle de cada uno.", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de `qp`.

    Codigos de salida: 0 correcto, 1 fallo del comando, 2 uso incorrecto. La
    distincion importa en CI: un uso incorrecto es un error del que invoca, no
    un fallo de la plataforma, y confundirlos hace que un pipeline mal escrito
    parezca una plataforma rota.
    """
    _use_utf8_output()
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help", "help"):
        sys.stdout.write(_usage())
        return 0

    command, rest = args[0], args[1:]

    if command == "version":
        sys.stdout.write(f"quantplatform {__version__}\n")
        return 0

    entry = COMMANDS.get(command)
    if entry is None:
        sys.stderr.write(f"Comando desconocido: {command!r}\n{_usage()}")
        return 2

    module_name = entry[0]
    try:
        module = __import__(module_name, fromlist=["main"])
    except ImportError as exc:
        # El comando esta declarado pero su fase todavia no lo implemento. Se
        # distingue de "comando desconocido" a proposito: uno es una errata del
        # usuario y el otro es una funcionalidad pendiente.
        sys.stderr.write(f"El comando {command!r} aun no esta implementado ({exc})\n")
        return 2

    run: Callable[[Sequence[str]], int] = module.main
    return run(rest)


__all__ = ["COMMANDS", "__version__", "main"]
