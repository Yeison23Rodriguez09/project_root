"""Nivel 6: puntos de entrada.

Traducen una peticion externa (linea de comandos, HTTP) en una llamada a un
caso de uso de `app/application`, y el resultado de vuelta a un formato
presentable.

No contienen logica. Una interfaz que decide algo es una interfaz que habra que
duplicar en cuanto aparezca el segundo punto de entrada.

Subpaquetes:

* `cli/` comandos de terminal, expuestos como `qp` en `[project.scripts]`
* `api/` endpoints de monitoreo, opcionales
"""
