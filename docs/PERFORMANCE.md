# Rendimiento medido

Medido el 2026-11-26 (Fase 8) con una **biblioteca sintética grande** («PRUEBA», generada solo para medir): 500 fuentes, **100 000 fragmentos** con su vector de significado, 2 000 conceptos, 20 000 afirmaciones con cita, 2 000 relaciones, 5 000 tarjetas y 20 000 repasos (base de datos de 880 MB).

Entorno: 4 núcleos, 15 GB de RAM, PostgreSQL 16 con la configuración por defecto (`shared_buffers` 128 MB), modelo de búsqueda local `multilingual-e5-small` int8. Cada operación se repite 5 veces tras una primera de calentamiento, a través de la API completa (sesión incluida).

| Operación | Mediana | Máximo |
|---|---:|---:|
| Buscar por palabras | 353 ms | 363 ms |
| Buscar por significado | 13 ms | 14 ms |
| Búsqueda combinada | 326 ms | 333 ms |
| Lista de fuentes | 7 ms | 8 ms |
| Conceptos (filtro) | 17 ms | 17 ms |
| Ficha de concepto | 22 ms | 25 ms |
| Grafo (vecindario a 2 saltos) | 13 ms | 14 ms |
| Comparación de explicaciones | 33 ms | 36 ms |
| Tarjetas por repasar | 37 ms | 39 ms |
| Informe de lagunas (2 000 conceptos) | 514 ms | 574 ms |
| Estado del sistema | 8 ms | 8 ms |
| Preparar una pregunta para la IA (24 fragmentos, fichas incluidas) | 764 ms | 803 ms |

**Lectura:**
- Todo está por debajo de 1 s con 100 000 fragmentos.
- La búsqueda por palabras es el caso peor: el texto sintético usa un vocabulario de ~90 palabras, así que casi todos los fragmentos coinciden con cualquier consulta y hay que ordenarlos todos. Con texto real, más variado, coincide una fracción y es más rápida.
- Preparar una pregunta para la IA tarda menos de 1 s; la respuesta del modelo (10-60 s) domina el tiempo total.

**Procesamiento (medido en fases anteriores):**
- PDF con Docling: depende de las páginas y tablas; el worker usa ~2 GB de RAM por documento.
- Transcripción: `large-v3-turbo` 2,3× tiempo real en 4 núcleos (1 h de clase ≈ 26 min); `small` 3,4× (D-18).
- Vectores de significado: ~1 ms por fragmento (D-15).

**Pendiente en el VPS:** repetir estas medidas allí con `scripts/perf/` (ver su README) y ajustar `shared_buffers` de PostgreSQL si el VPS tiene memoria de sobra.
