# Seguridad

Aplicación de **un solo usuario** expuesta a internet en tu VPS. Este documento resume qué se protege, cómo, qué prueba lo comprueba y qué riesgos quedan. Revisado el 2026-11-26 (Fase 8).

## Acceso
| Riesgo | Protección | Prueba |
|---|---|---|
| Usar la API sin sesión | Las 108 rutas de la API exigen sesión salvo `/api/health`, login y logout. La prueba recorre **todas** las rutas automáticamente: una ruta nueva sin protección la hace fallar. | `test_security.py::test_no_route_works_without_a_session` |
| Adivinar la contraseña | argon2; bloqueo tras intentos fallidos por usuario e IP; la contraseña la eliges tú (≥ 12 caracteres). | `test_auth.py` |
| Robo de la sesión | Cookie `HttpOnly`, `SameSite=Lax`, `Secure` en producción; solo se guarda el hash del token; caduca a los 14 días; «Salir» la invalida. | `test_auth.py` |
| Peticiones desde otra web (CSRF) | Toda petición que cambia datos con la cookie debe venir de tu propio origen (`KOS_APP_ORIGIN`). | `test_auth.py::test_cross_origin_post_with_cookie_is_rejected` |
| Clientes MCP | Token propio `kos_…` (solo su hash en la base de datos), revocable, con permiso de solo lectura o de proponer; protección contra *DNS rebinding*. Nada de lo que escribe se acepta solo. | `test_mcp.py`, `test_security.py` |

## Ficheros que subes
| Riesgo | Protección | Prueba |
|---|---|---|
| HTML o scripts disfrazados | El formato se decide por el contenido, no por la extensión; no se admite HTML ni SVG; el texto se sirve como `text/plain` con `nosniff`, CSP `sandbox` y como descarga. | `test_security.py` (varios) |
| Bombas ZIP (DOCX/PPTX/EPUB) | Se rechaza si descomprimido supera 2 GB o 20 000 entradas. | `test_formats.py` |
| Nombres de fichero maliciosos | Se guarda solo el nombre final, sin caracteres de control; el almacén usa el hash como nombre y no puede salirse de su carpeta. | `test_security.py::test_filenames_cannot_escape_or_inject` |
| Documentos que leen ficheros locales o llaman a la red (XXE/SSRF) | Comprobado con EPUB y DOCX con entidades externas, imágenes, hojas de estilo y marcos remotos: no se lee nada local ni se hace ninguna petición. | `test_security.py::test_documents_cannot_read_local_files_or_reach_the_network` |
| PDF enormes o cuelgues | Máximo 5000 páginas; Docling con tiempo límite; si falla, extracción básica. | `test_docling.py` |

## Inyección de instrucciones en las fuentes (prompt injection)
- El texto de tus fuentes va siempre como **datos delimitados** (`<source_document>`), nunca en las instrucciones; se escapan tanto el cierre como la apertura de la etiqueta, para que un documento no pueda cerrar el suyo ni fingir otro. El modelo recibe el aviso de que ese contenido no son órdenes.
- El modelo **no tiene herramientas**: solo devuelve datos con un esquema fijo.
- Lo que devuelve **nunca se acepta solo**: conceptos `propuestos`, afirmaciones `pendientes`, tarjetas `propuestas`; toda cita debe aparecer literalmente en el fragmento (si no, se descarta).
- Pruebas: `test_security.py::test_source_text_cannot_escape_its_delimiters`, `::test_ai_output_never_becomes_accepted_knowledge`.

## Peticiones del servidor a otros sitios (SSRF)
El servidor solo sale a internet en tres casos, todos con destino fijo:
- **Crossref** (`api.crossref.org`): solo se envía un DOI validado (sin segmentos `.`/`..`) como ruta. Prueba: `test_doi_lookup_only_ever_calls_crossref`.
- **YouTube**: solo se aceptan enlaces de YouTube y el worker reconstruye la URL a partir del identificador de 11 caracteres. Prueba: `test_youtube.py`, `test_source_urls_are_metadata_and_never_fetched`.
- **Anthropic** (la IA) y **Hugging Face** (descarga de modelos, solo con el comando explícito).
- El campo «URL» de una fuente es solo un dato: nunca se descarga. Prueba: `test_source_urls_are_metadata_and_never_fetched`.

## Navegador
Cabeceras en todas las páginas: CSP (solo recursos propios; el único tercero incrustado es el reproductor `youtube-nocookie.com`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`. Comprobado en navegador: ninguna página incumple la CSP. HTTPS y HSTS los pone tu Caddy; **HSTS hay que pedirlo** con `header Strict-Transport-Security` en el bloque del sitio, porque Caddy no lo envía por defecto (ver DEPLOYMENT.md §3).

## Infraestructura
Contenedores sin root; la base de datos no se publica; el gateway escucha solo en `127.0.0.1` (tu Caddy es la única entrada); `.env` con permisos 600 y fuera de git; copias de seguridad cifrables y verificadas (`scripts/backup.sh`).

## Dependencias
- Python: `pip-audit` sin vulnerabilidades conocidas (torch/torchvision de la web de PyTorch no se pueden auditar con esa herramienta).
- Web: `npm audit` sin vulnerabilidades (se fuerzan `postcss` ≥ 8.5.23 y `katex` ≥ 0.18.2 mediante `overrides`).
- Repetir tras cada actualización: ver DEPLOYMENT.md §6.

## Riesgos que quedan
- **yt-dlp** descarga de YouTube y se actualiza a menudo; un fallo suyo afectaría al worker (que no tiene acceso a la base de datos más allá de la suya ni a tus credenciales de IA salvo la clave).
- La CSP permite `'unsafe-inline'` en scripts porque Next.js lo necesita para hidratar; no se carga ningún script externo.
- El límite de gasto de IA se comprueba antes de cada llamada; con varias llamadas simultáneas puede superarse ligeramente.
- No hay segundo factor de autenticación (usuario único); si lo quieres, se puede añadir delante con tu Caddy (p. ej. autenticación básica adicional o un proveedor de identidad).

## Clave de Anthropic guardada desde la web (2026-11-27)
- Se puede poner en la página IA en lugar del `.env`. Se guarda cifrada (Fernet: AES-128 + HMAC) con una clave derivada de `KOS_SECRET_KEY`, que solo está en el `.env` del servidor: un volcado o una copia de la base de datos no la revela por sí solo.
- La API nunca la devuelve: solo sus 4 últimos caracteres. No se escribe en registros ni en la auditoría (que guarda únicamente esos 4 caracteres). El campo de la web es de tipo contraseña.
- Si hay clave en el `.env`, manda esa. Cambiar `KOS_SECRET_KEY` deja ilegible la clave guardada (la página lo avisa); hay que volver a introducirla.
- Recomendación: crearla en console.anthropic.com con límite de gasto mensual, además del límite de la página IA.
