"""Capas de la arquitectura limpia.

    core            primitivas, sin dependencias internas
    domain          entidades y reglas puras
    shared          puertos y catalogo de componentes
    application     casos de uso y orquestacion
    infrastructure  adaptadores con I/O
    interfaces      puntos de entrada (CLI, API)

La regla de dependencia apunta siempre hacia dentro. `docs/ARCHITECTURE.md`
detalla los niveles y las prohibiciones que verifica
`tests/test_architecture.py`.
"""

__version__ = "1.0.0"
