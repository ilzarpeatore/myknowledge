# Guía de uso de Knowledge OS

Tu base de conocimiento personal sobre entrenamiento: guardas tus cursos, libros, estudios y clases; la aplicación los convierte en texto que se puede citar, organiza los conceptos y te ayuda a estudiarlos. **Todo lo que afirma remite a la fuente exacta** (página, diapositiva o minuto) y lo que propone la IA no cuenta como conocimiento hasta que tú lo revisas.

## 1. El flujo de trabajo, de un vistazo
1. **Biblioteca:** sube tus materiales.
2. **Aprendizaje:** organízalos en formaciones, cursos, módulos y lecciones.
3. **Extraer conocimiento** (IA) o crear conceptos y afirmaciones a mano.
4. **Revisión:** acepta, corrige o descarta lo propuesto.
5. **Conceptos, Grafo, Árbol, Mapas:** explora lo que sabes y cómo se relaciona.
6. **Preguntar:** consulta a tu biblioteca con respuestas citadas.
7. **Estudiar:** tarjetas con repetición espaciada, cuestionarios y tus lagunas.

## 2. Biblioteca
- **Qué puedes subir:** PDF (también escaneados: se aplica OCR), Word (DOCX), PowerPoint (PPTX), EPUB, Markdown, TXT, subtítulos (SRT/VTT), audio y vídeo (se transcriben en tu servidor, sin coste de IA).
- **YouTube:** pega el enlace en «Añadir vídeo de YouTube». Se guardan los subtítulos del autor; si no hay, los automáticos en el idioma original; si tampoco, se transcribe el audio. Si YouTube bloquea al servidor, el error lo explica: descarga los subtítulos o el audio en tu ordenador y súbelos.
- **El original no se modifica nunca.** Si subes dos veces el mismo fichero, se detecta.
- El estado de cada fuente: *En cola*, *En proceso*, *Listo* o *Error* (con el motivo). «Reprocesar» lo intenta de nuevo.
- **Vincular a estudio:** si un PDF es un artículo científico, vincúlalo a su ficha en **Estudios**; sus afirmaciones contarán como del estudio.

### Secciones
La biblioteca se organiza en **secciones**: Cursos, Libros, Manuales, Estudios, Artículos, Vídeos, Audios, Apuntes y Recursos (puedes renombrarlas y crear las tuyas con «+ nueva sección»).
- Lo que subes desde una sección queda en ella. Si subes desde «Todo», se coloca solo: vídeos y YouTube en Vídeos, audio en Audios, EPUB en Libros, lo demás en Recursos; al vincularlo a un estudio se añade a Estudios y al asociarlo a un curso, a Cursos.
- Una fuente puede estar en **varias secciones** (una clase grabada: Cursos y Vídeos). Cámbialas en su página («conexiones →»).
- **Cruzar secciones:** la página de cada fuente muestra sus conceptos y lo **relacionado en otras secciones** (fuentes que tratan los mismos conceptos, agrupadas por sección) y te deja **enlazarla** a mano con otra («libro que recomienda el curso»). En la ficha de cada concepto, **Dónde aparece** lo agrupa por sección. Buscar y Preguntar se pueden limitar a una sección.

## 3. Buscar y preguntar
- **Buscar:** por palabras, por significado (encuentra sinónimos y otros idiomas) o combinada. Cada resultado abre la fuente en su sitio. «Usar como evidencia» lleva el fragmento a la ficha de un concepto.
- **Preguntar:** escribe una pregunta y elige el ámbito (toda la biblioteca, un curso, una disciplina, una fuente o un concepto) y el modo:
  - *Investigación:* solo lo que dice tu biblioteca.
  - *Aplicación:* además, inferencias y conocimiento general, siempre marcados.

  La respuesta se divide por colores: **De tus fuentes** (con cita literal comprobada), **Síntesis**, **Inferencia de la IA** y **Conocimiento externo**, e indica si la biblioteca cubre la pregunta. «Seguir sobre lo anterior» mantiene el tema («¿En qué coinciden?»).

## 4. Conceptos y revisión
- Un **concepto** tiene nombre, alias (otros nombres, traducciones, siglas), definición, **afirmaciones** (cada una con su cita literal) y **relaciones** con otros conceptos.
- Cada afirmación indica su **origen**: estudio (con su calidad metodológica), profesor, inferencia de IA, tu conclusión o fuente sin atribuir. En la ficha puedes filtrar «Respaldado por estudios» frente a «Explicaciones de profesores».
- **Extraer conocimiento** (botón en cada fuente de la Biblioteca): la IA propone conceptos y afirmaciones fragmento a fragmento. Antes de empezar ves el coste estimado. Si se para (por presupuesto o error), «Continuar» sigue donde lo dejó.
- **Revisión:** posibles duplicados (puedes fusionar; las fusiones se deshacen), relaciones y contradicciones propuestas. En la ficha de cada concepto: aceptar o descartar el concepto y aceptar o rechazar cada afirmación pendiente. **Nada se borra**: lo rechazado queda registrado.
- **Comparar explicaciones:** en la ficha, cómo lo explica cada profesor, curso o estudio, con el contexto de cada uno; «Analizar con IA» señala coincidencias, diferencias y posibles contradicciones, que tú clasificas (contradicción real, diferencia de contexto o descartada).

## 5. Explorar
- **Grafo:** el concepto y sus vecinos, filtrable por tipo de relación y disciplina.
- **Árbol:** jerarquía «es un tipo de» y «es parte de».
- **Mapa mental** (desde la ficha): ramas por tipo de relación; se exporta en SVG, Markdown u OPML.

## 6. Estudios científicos
En **Estudios** añade un artículo por DOI (se consultan sus datos en Crossref) o a mano. Rellena su ficha metodológica (tipo, diseño, población, muestra, resultados, limitaciones) y **evalúa tú su calidad**: empieza como «no evaluada» y el sistema nunca la deduce.

## 7. Aprendizaje y estudio
- **Aprendizaje:** formaciones, cursos, módulos y lecciones, con su progreso, tiempo y apuntes; asocia a cada lección sus materiales. Cada curso muestra sus conceptos.
- **Tarjetas:** en la ficha de un concepto, «Crear tarjetas de las afirmaciones» (gratis, una por afirmación aceptada) o «Proponer más con IA» (se aceptan en Estudiar → Propuestas).
- **Repasar** (Estudiar): lee la pregunta, «Mostrar respuesta» (con su cita y fuente) y valora: *Otra vez*, *Difícil*, *Bien* o *Fácil*. Cada botón indica cuándo volverá la tarjeta. Si configuras los avisos, recibes cada mañana cuántas tienes pendientes.
- **Cuestionarios:** en la página del curso, «Cuestionario del módulo (IA)». Las preguntas de opción y verdadero/falso se corrigen solas; las abiertas las comparas tú con la respuesta modelo. Cada corrección muestra el pasaje de la fuente.
- **Lagunas:** separa lo que **tu biblioteca** no cubre bien (pocos materiales) de lo que **te cuesta a ti** (fallos en repasos o cuestionarios). También por curso.

## 8. IA y coste
- En **IA** ves el gasto del mes, el límite (cámbialo ahí) y cada llamada (modelo, tokens, coste). **Al llegar al límite, los trabajos de IA se detienen** solos; los que se pueden continuar lo hacen al subirlo o al empezar el mes.
- Puedes cambiar el modelo de cada tipo de tarea sin tocar código.
- La IA es opcional: biblioteca, búsqueda, conceptos manuales, tarjetas automáticas, repaso y lagunas funcionan sin ella.

## 9. Usar tu biblioteca desde Claude (MCP)
En **IA → Conectar Claude (MCP)** crea un token y copia el comando para Claude Code (o la configuración de Claude Desktop). Claude podrá buscar, leer y comparar; si le das permiso de propuestas, lo que proponga llega a Revisión como pendiente. Revoca el token cuando quieras.

## 10. Sistema
La página **Sistema** (enlace en IA y aviso en Inicio si algo falla) muestra el worker y lo que sabe hacer, la cola de trabajos, el disco, la última copia de seguridad y el gasto de IA.

## 11. Buenas prácticas
- Revisa las propuestas de la IA con la fuente delante: la cita literal está para eso.
- Vincula los PDF de estudios a su ficha y evalúa su calidad: mejora las respuestas y el filtro «respaldado por estudios».
- Asocia los materiales a sus lecciones: así funcionan los cuestionarios por módulo y las lagunas por curso.
- Mantén las copias de seguridad fuera del servidor (DEPLOYMENT.md §4).
