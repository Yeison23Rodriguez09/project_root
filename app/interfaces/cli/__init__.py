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

__version__ = "0.5.0"

#: Comando -> (modulo, descripcion). El modulo se importa solo al invocarse.
COMMANDS: dict[str, tuple[str, str]] = {
    "doctor": ("app.interfaces.cli.doctor", "comprueba que la plataforma este bien construida"),
    "preflight": ("app.interfaces.cli.preflight", "valida el despliegue antes de operar"),
    "status": ("app.interfaces.cli.status", "estado calculado de la plataforma"),
    "config": ("app.interfaces.cli.config_cmd", "muestra y valida la configuracion efectiva"),
    "version": ("", "version de la plataforma"),
}


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
