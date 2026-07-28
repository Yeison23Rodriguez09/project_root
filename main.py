"""Punto de entrada del proyecto.

Deliberadamente vacio de logica. Su unica funcion es delegar en la CLI, de modo
que `python main.py` y el comando instalado `qp` recorran exactamente el mismo
camino. Si divergieran, un fallo podria reproducirse por una via y no por la
otra, y eso es precisamente lo que este proyecto no puede permitirse.
"""

from __future__ import annotations

import sys


def main() -> int:
    from app.interfaces.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
