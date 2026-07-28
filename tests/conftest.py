"""Configuracion comun de la suite.

Anade la raiz del repositorio a `sys.path` para que los tests corran sobre el
arbol de trabajo y no sobre una version instalada en el entorno. La diferencia
importa: si pytest importara el paquete instalado, un cambio sin reinstalar
pasaria los tests sin haberse probado nunca.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
