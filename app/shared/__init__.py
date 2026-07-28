"""Nivel 2: puertos y utilidades transversales.

`ports.py` declara los `Protocol` que separan el dominio del mundo exterior.
`registry.py` implementa el catalogo de componentes con espacio de parametros.

Se usan `Protocol` estructurales y no clases base abstractas porque permiten
que un adaptador cumpla el contrato sin heredar de nuestro codigo, permiten
sustituir cualquier puerto por un doble de test sin registro previo, y mypy los
verifica en analisis estatico sin coste en ejecucion.

Consecuencia practica: backtest, paper y live comparten el mismo caso de uso.
Lo unico que cambia es si `BrokerPort` lo implementa un simulador o MT5.
"""
