"""Configuracion: la mitad de infraestructura. Proveedores y ensamblado.

Unico codigo del sistema autorizado a leer ficheros de configuracion, variables
de entorno y argumentos de linea de comandos.

Esta en la capa `infrastructure` (nivel 4) a proposito. Los motores estan en el
nivel 3 y ninguno lo declara en su `depends`, asi que el validador AST impide que
un motor importe este paquete. No es una convencion que haya que recordar: es una
prueba que rompe el build. Ver ADR-0005.

Los motores reciben configuracion ya resuelta, tipada y validada desde
`app/application`.

Modulos:

* `providers.py`  TOML, YAML, entorno, linea de comandos e inyeccion en memoria
"""
