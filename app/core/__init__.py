"""Nivel 0: primitivas sin dependencias internas.

Contiene tipos, excepciones, matematica pura, aritmetica temporal, determinismo
y el contenedor de validacion. Ningun modulo de `app.core` importa de
`app.domain`, `app.application`, `app.infrastructure` ni de ningun motor. Esa
restriccion es la base mecanica de `dependency_rule = "outward_only"` y se
verifica en `tests/test_architecture.py`.

No se reexporta nada a proposito: los imports son explicitos desde el submodulo
concreto (`from app.core.types import Symbol`), de modo que la procedencia de
cada simbolo sea legible en el punto de uso y un traslado futuro no quede
oculto tras un alias.
"""
