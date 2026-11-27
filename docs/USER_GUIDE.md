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
- **Qué puedes subir:** PDF (también escaneados: se aplica OCR), Word (DOCX), PowerPoint (PPTX), EPUB, Markdown, TXT, subtítulos (SRT/VTT), audio y vídeo (se transcriben en tu servidor, sin coste de IA; el idioma, español o inglés, se detecta solo).
- **YouTube:** pega el enlace en «Añadir vídeo de YouTube». Se guardan los subtítulos del autor; si no hay, los automáticos en el idioma original; si tampoco, se transcribe el audio. En vídeos doblados se usa siempre el idioma original. Si una transcripción sale mal (otro idioma, incompleta), «Volver a descargar de YouTube» en la página de la fuente la sustituye. Si YouTube bloquea al servidor, el error lo explica: descarga los subtítulos o el audio en tu ordenador y súbelos.
- **El original no se modifica nunca.** Si subes dos veces el mismo fichero, se detecta.
- **Eliminar:** «Archivar» la lleva a la papelera (se puede restaurar). Para borrarla del todo, marca «Ver archivados (papelera)» y pulsa «Eliminar definitivamente»: antes te dice qué se perderá (archivos, fragmentos, afirmaciones que solo se apoyaban en ella, tarjetas). Los conceptos se conservan y las afirmaciones con citas de otras fuentes también. No se puede deshacer salvo con una copia de seguridad anterior.
- El estado de cada fuente: *En cola*, *En proceso*, *Listo* o *Error* (con el motivo). «Reprocesar» lo intenta de nuevo.
- **Vincular a estudio:** si un PDF es un artículo científico, vincúlalo a su ficha en **Estudios**; sus afirmaciones contarán como del estudio.

### Secciones
La biblioteca se organiza en **secciones**: Cursos, Libros, Manuales, Estudios, Artículos, Vídeos, Audios, Apuntes y Recursos (puedes renombrarlas y crear las tuyas con «+ nueva sección»).
- Lo que subes desde una sección queda en ella. Si subes desde «Todo», se coloca solo: vídeos y YouTube en Vídeos, audio en Audios, EPUB en Libros, lo demás en Recursos; al vincularlo a un estudio se añade a Estudios y al asociarlo a un curso, a Cursos.
- Una fuente puede estar en **varias secciones** (una clase grabada: Cursos y Vídeos). Cámbialas en su página («conexiones →»).
- **Cruzar secciones:** la página de cada fuente muestra sus conceptos y lo **relacionado en otras secciones** (fuentes que tratan los mismos conceptos, agrupadas por sección) y te deja **enlazarla** a mano con otra («libro que recomienda el curso»). En la ficha de cada concepto, **Dónde aparece** lo agrupa por sección. Buscar y Preguntar se pueden limitar a una sección.

### Ramas (Nutrición, Biomecánica, Fisiología…)
Además de la sección (qué tipo de material es), cada fuente puede tener una o varias **ramas** (de qué trata).
- **Al subir:** marca las ramas en «Rama de lo que subas»; se aplican a todo lo que subas o añadas de YouTube hasta que las cambies. Si aún no hay ramas, «Crear las de Ciencias del entrenamiento» crea las habituales (Anatomía, Biomecánica, Fisiología, Nutrición…); «+ nueva rama» añade las tuyas.
- **Después:** en «Editar» de la biblioteca o en la página de la fuente («Rama»).
- **Filtrar:** en la biblioteca y en Buscar («Todas las ramas»); en Preguntar, ámbito «Una disciplina». Una rama incluye sus sub-ramas y los materiales de los cursos clasificados en ella. Claude (MCP) también puede filtrar por rama.

### Leer los PDF sin salir de la página
- La página de cada fuente en PDF (y la ficha de cada estudio con su PDF vinculado) muestra el documento dentro de la propia página: pasar páginas, zoom, buscar (Ctrl+F), miniaturas e índice (☰), descargar e imprimir. «Pantalla completa» lo abre a todo lo alto.
- **Las citas abren el PDF en la página y con la frase citada resaltada** (desde conceptos, definiciones, respuestas, tarjetas…). Si la frase no se encuentra tal cual, se prueba con sus primeras palabras y, si no, se abre en la página.
- El visor es Inkbar (licencia MIT), que funciona sobre PDF.js en tu navegador: no gasta IA ni carga el servidor. La primera vez descarga unos 1,5 MB, que quedan en la caché. Si un PDF no se puede abrir en él, aparece un enlace para abrirlo en el visor del navegador.
- Word, PowerPoint y EPUB siguen abriéndose como archivo original.

### Autores
- Al subir un archivo o un vídeo de YouTube puedes indicar **el autor o los autores** (en un estudio, en el orden del artículo). Escribe unas letras y aparecen las coincidencias de tu biblioteca de autores, sin importar tildes ni mayúsculas («lopez» encuentra «José López Chicharro», «schoen» encuentra «Brad J. Schoenfeld»; si te equivocas al escribir, también aparece el parecido). Con ↑ ↓ eliges y con Enter lo añades; si no existe, «Añadir como autor nuevo». Varios a la vez: sepáralos con «;». El orden se cambia con ◂.
- Un nombre que ya existe (aunque lo escribas con otras mayúsculas o sin tildes) **no se duplica**: se usa el que hay. Los autores nuevos toman el papel según la sección (Estudios → investigador, Cursos y Vídeos → profesor, Libros → autor); se cambia después.
- En «Editar» de cada fuente cambias sus autores. Si tenía autores solo como texto, «Convertir en autores» los separa para que los revises.
- **Autores** (menú): buscador que responde mientras escribes, filtro por papel, cuántas fuentes y afirmaciones tiene cada uno. En la ficha de cada autor: sus fuentes (y con quién las firma), su postura por concepto y frente a los estudios, **editar** (nombre, papel, afiliación), **fusionar** duplicados escritos de otra forma («B. Schoenfeld» en «Brad J. Schoenfeld»: sus fuentes y afirmaciones pasan al que se queda) y borrar uno creado por error.
- En la Biblioteca puedes **filtrar por autor**, y el nombre de cada autor en la lista lleva a su ficha.

### Referencias bibliográficas
En **Referencias** están todas las obras citadas en tus documentos: citas en diapositivas o en el texto («Schoenfeld et al., 2017», «Helms (2016)»), bibliografías de libros y apuntes, y DOI. Se detectan solas al subir cada documento, sin IA ni coste.
- **Contador:** cuántas veces aparece cada obra y en cuántos documentos (y cuántas veces en una bibliografía). Ordena por «más citadas» para ver qué estudios mencionan más tus profesores; filtra por sección o por si ya está en Estudios.
- **Ficha de cada referencia:** cada mención con su contexto y enlace al sitio exacto. «Añadir a Estudios» crea la ficha del estudio (con los datos de Crossref si hay DOI) y la vincula; si ya tienes el estudio, te lo propone.
- **Corregir:** completa título, autores, DOI o año; «No es una referencia» la descarta; «Unir con esta» junta dos referencias que son la misma obra. Se recuerda al volver a analizar.
- En la página de cada fuente aparecen las referencias que cita.

## 3. Buscar y preguntar
- **Buscar:** por palabras, por significado (encuentra sinónimos y otros idiomas) o combinada. Cada resultado abre la fuente en su sitio. «Usar como evidencia» lleva el fragmento a la ficha de un concepto.
- **Preguntar:** escribe una pregunta y elige el ámbito (toda la biblioteca, un curso, una disciplina, una fuente o un concepto) y el modo:
  - *Investigación:* solo lo que dice tu biblioteca.
  - *Aplicación:* además, inferencias y conocimiento general, siempre marcados.

  La respuesta se divide por colores: **De tus fuentes** (con cita literal comprobada), **Síntesis**, **Inferencia de la IA** y **Conocimiento externo**, e indica si la biblioteca cubre la pregunta. «Seguir sobre lo anterior» mantiene el tema («¿En qué coinciden?»).

## 4. Conceptos y revisión
- Un **concepto** tiene nombre, alias (otros nombres, traducciones, siglas), definición, **afirmaciones** (cada una con su cita literal) y **relaciones** con otros conceptos.
- **Definición** (arriba en la ficha): qué es el concepto, en una o dos frases, con una **explicación ampliada** opcional. Puedes:
  - **Escribirla tú** (o «Editar» la que hay).
  - **Generar con IA**: la IA la redacta a partir de tus fuentes (las afirmaciones del concepto y los fragmentos que lo tratan) y cita los pasajes en que se basa; cada cita se comprueba letra a letra en el documento y la que no aparece se quita. Indica si está **basada en tus fuentes**, si las completa con **conocimiento general** o si es **solo conocimiento general** (tus fuentes no la respaldan), y deja una nota cuando las fuentes discrepan. Queda como **propuesta**: «Aceptar», «Editar y aceptar» o «Descartar». Nunca sustituye tu definición sin ti. El modelo se elige en IA → tarea «Definir conceptos».
  - **Cómo lo definen tus fuentes**: las afirmaciones de tipo *definición* de cada profesor, libro o estudio, con su cita; «Usar como punto de partida» la copia al editor.
  - **Definiciones anteriores**: cada cambio queda guardado y puedes **recuperar** cualquiera.
  - Claude (MCP) también puede proponer definiciones; llegan igual, como propuestas.
- Cada afirmación indica su **origen**: estudio (con su calidad metodológica), profesor, inferencia de IA, tu conclusión o fuente sin atribuir. En la ficha puedes filtrar «Respaldado por estudios» frente a «Explicaciones de profesores».
- **Extraer conocimiento** (botón en cada fuente de la Biblioteca): la IA propone conceptos y afirmaciones fragmento a fragmento. Antes de empezar ves el coste estimado. Si se para (por presupuesto o error), «Continuar» sigue donde lo dejó.
- **Revisión:** posibles duplicados (puedes fusionar; las fusiones se deshacen), relaciones y contradicciones propuestas. En la ficha de cada concepto: aceptar o descartar el concepto y aceptar o rechazar cada afirmación pendiente. **Nada se borra**: lo rechazado queda registrado.
- **Comparar explicaciones:** en la ficha, cómo lo explica cada profesor, curso o estudio, con el contexto de cada uno; «Analizar con IA» señala coincidencias, diferencias y posibles contradicciones, que tú clasificas (contradicción real, diferencia de contexto o descartada).

### Cruces y análisis
- **Ver también** (en cada fragmento de Buscar y en cada cita de un concepto): pasajes de otras fuentes que dicen lo mismo, también en otro idioma. Sin IA.
- **Consenso** (ficha del concepto): las afirmaciones que dicen lo mismo, agrupadas, con cuántas fuentes y estudios las sostienen, un **nivel de evidencia** calculado solo con la calidad que tú diste a cada estudio, y si está **en disputa**. Las afirmaciones contradictorias nunca se agrupan juntas.
- **Frente a los estudios:** cada afirmación de un profesor o curso se compara con lo que dicen tus estudios: *respaldada*, *en parte*, *contradicha*, *hay estudios sin comprobar* o *sin estudios*. Lo propone la IA («Comprobar con estudios» en la ficha, o al analizar una fuente) y tú lo confirmas («Correcto» / «No es así»).
- **Al terminar de extraer el conocimiento de una fuente** se cruza sola con la biblioteca: propone enlaces con otras fuentes (amplía, resume, contradice, aplica a la práctica), posibles contradicciones y comprobaciones frente a estudios. Todo llega a **Revisión**; nada se da por bueno sin ti. Un enlace descartado no se vuelve a proponer.
- **Qué aporta** (página de la fuente): conceptos nuevos que no trata ninguna otra fuente, afirmaciones que coinciden con otras, las que están en contradicción, cómo quedan frente a los estudios y cuántas referencias cita.
- **Análisis** (menú): **mapa de cobertura** (qué fuentes tratan cada concepto, con los conceptos de una sola voz marcados), enlaces propuestos y la lista de profesores. El **perfil de cada profesor** muestra su postura por concepto, cómo queda frente a los estudios y con quién coincide o discrepa.

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
- **Clave de Anthropic:** en la página **IA** pega tu clave y pulsa «Guardar clave»; se guarda cifrada y no se vuelve a mostrar (solo sus 4 últimas letras). «Probar la conexión» hace una llamada mínima; «Quitar clave» la borra. Si el servidor tiene una clave en su configuración, manda esa.
- En **IA** ves el gasto del mes, el límite (cámbialo ahí) y cada llamada (modelo, tokens, coste). **Al llegar al límite, los trabajos de IA se detienen** solos; los que se pueden continuar lo hacen al subirlo o al empezar el mes.
- Puedes cambiar el modelo de cada tipo de tarea sin tocar código.
- La IA es opcional: biblioteca, búsqueda, conceptos manuales, tarjetas automáticas, repaso y lagunas funcionan sin ella.

## 9. Usar tu biblioteca desde Claude (MCP)
En **IA → Conectar Claude (MCP)** crea un token y copia el comando para Claude Code (o la configuración de Claude Desktop). El trabajo lo hace Claude con tu suscripción, sin gastar API. Hay tres niveles de token:
- **Solo leer:** buscar, leer fragmentos, conceptos, comparar explicaciones, ver conexiones y lo pendiente de revisar.
- **Escribir:** además, proponer conceptos, afirmaciones con cita, relaciones, **contradicciones**, **fusiones de duplicados**, **comprobaciones frente a estudios**, **enlaces entre fuentes** y **definiciones de conceptos** (todo llega como pendiente), y **guardar síntesis o mapas** como notas en Apuntes.
- **Decidir:** además, aceptar o rechazar lo pendiente cuando tú se lo pidas. Todo queda registrado con el nombre del token.

Revoca el token cuando quieras. Ejemplos: «Compara cómo explican la proximidad al fallo el libro y el estudio y propón las contradicciones», «Busca conceptos duplicados y propón fusiones», «Hazme una síntesis de la fatiga y guárdala en Apuntes», «Propón definiciones para los conceptos de fisiología que no tengan, citando mis fuentes».

**Para gastar poco de tu plan:** pide trabajo por temas o por conceptos, no «toda la biblioteca»; las respuestas vienen recortadas y por páginas, así que Claude solo pide más cuando lo necesita; las propuestas van por lotes (hasta 25 por llamada) y lo ya propuesto no se repite. Con muchos conectores MCP activos a la vez, cada mensaje pesa más: desactiva los que no uses en esa conversación.

## 10. Sistema
La página **Sistema** (enlace en IA y aviso en Inicio si algo falla) muestra el worker y lo que sabe hacer, la cola de trabajos, el disco, la última copia de seguridad y el gasto de IA.

## 11. Buenas prácticas
- Revisa las propuestas de la IA con la fuente delante: la cita literal está para eso.
- Vincula los PDF de estudios a su ficha y evalúa su calidad: mejora las respuestas y el filtro «respaldado por estudios».
- Asocia los materiales a sus lecciones: así funcionan los cuestionarios por módulo y las lagunas por curso.
- Mantén las copias de seguridad fuera del servidor (DEPLOYMENT.md §4).
