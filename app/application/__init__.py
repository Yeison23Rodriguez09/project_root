"""Nivel 4: casos de uso y orquestacion.

Aqui vive la secuencia, no la regla. Un runner sabe en que orden ocurren las
cosas y quien depende de quien; no sabe calcular un indicador, dimensionar una
posicion ni decidir una promocion. Si un runner empieza a contener aritmetica
de negocio, esa aritmetica pertenece a `app/domain/services`.

Los runners reciben sus dependencias como puertos, nunca como implementaciones
concretas. Por eso el mismo `backtest_runner` funciona con datos de fichero o
de broker sin conocer la diferencia.

Contenido previsto:

* `backtest_runner.py`  orquesta el motor bar a bar sobre un rango cerrado
* `discovery_runner.py` orquesta generacion, evaluacion y ranking de candidatos
* `live_runner.py`      ciclo continuo de live y paper
* `promotion_runner.py` evaluacion cientifica y paso al zoo
"""
