# Despliegue en el VPS

Guía para instalar Knowledge OS en un servidor Ubuntu con Docker y Caddy, sin modificar los servicios que ya funcionan en él.

> **Lo comprobado (2026-11-26).** Con Docker en el entorno de desarrollo se han construido las tres imágenes y levantado el sistema completo con este `docker-compose.yml`: migraciones, API sana, procesador (Docling con tablas y OCR **sin acceso a internet**), web y gateway; subida de 40 MB a través del gateway; búsqueda por palabras y por significado; `backup.sh` y `restore.sh` en modo Docker (recuentos idénticos tras restaurar, PDF original idéntico byte a byte, propietarios de ficheros correctos). Lo que solo se verá en el VPS: tu Caddy delante, HTTPS y el rendimiento con tu CPU.
>
> **Medido:** imágenes `app` ~0,95 GB, `worker` ~4,7 GB (PyTorch CPU 0,8 GB + modelos 0,8 GB), `web` ~0,35 GB; total ~6,4 GB de disco. RAM en reposo de todo el sistema ~250 MB; ~2 GB extra mientras se procesa un PDF y ~0,3 GB en la API tras la primera búsqueda por significado.

## 0. Antes de tocar nada: inventario (solo lectura)

El repositorio es **privado**: el VPS necesita una clave de solo lectura («deploy key»), que da acceso a este repositorio y a nada más.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/myknowledge_deploy -N "" -C "vps myknowledge (solo lectura)"
cat ~/.ssh/myknowledge_deploy.pub
```

En GitHub: repositorio → *Settings* → *Deploy keys* → *Add deploy key*, pega la clave pública y deja **sin marcar** «Allow write access». Después:

```bash
cat >> ~/.ssh/config <<'CFG'
Host github-myknowledge
    HostName github.com
    User git
    IdentityFile ~/.ssh/myknowledge_deploy
    IdentitiesOnly yes
CFG
git clone git@github-myknowledge:ilzarpeatore/myknowledge.git
cd myknowledge
git checkout main          # o la rama que vayas a desplegar
./scripts/preflight.sh conocimiento.tudominio.com
```

`preflight.sh` **no cambia nada**: escribe un informe `preflight-<fecha>.txt` con sistema, CPU, RAM, disco, contenedores y redes Docker, puertos en uso, sitios de Caddy (solo las direcciones), cortafuegos y DNS del dominio. Revísalo (o compártelo) antes de seguir. Puntos a confirmar:

| Comprobación | Necesario |
|---|---|
| RAM libre | ≥ 4 GB (el procesador de documentos usa ~2 GB por PDF; la API ~0,5 GB) |
| Disco libre | ≥ 15 GB (imágenes ~6,4 GB, más la caché de construcción), más el tamaño de tus documentos y copias |
| CPU | AVX2 (todas las x86-64 modernas); AVX-512 VNNI acelera el modelo de búsqueda pero no es obligatorio (ver D-15) |
| Puerto | `KOS_HTTP_PORT` (por defecto 3000) libre en `127.0.0.1`; si no, elige otro |
| Caddy | Saber si es servicio del sistema o contenedor, y dónde está su Caddyfile |
| DNS | El subdominio apunta a la IP del VPS |

## 1. Configuración

```bash
cp .env.example .env
openssl rand -hex 32         # pega el resultado en KOS_DB_PASSWORD (hex: va dentro de una URL, «/» o «+» la romperían)
nano .env
```

| Variable | Valor |
|---|---|
| `KOS_DB_PASSWORD` | la generada arriba |
| `KOS_APP_ORIGIN` | `https://conocimiento.tudominio.com` (exacta, con `https://`) |
| `KOS_COOKIE_SECURE` | `true` |
| `KOS_HTTP_PORT` | 3000 u otro puerto libre |
| `ANTHROPIC_API_KEY` | tu clave (puede quedar vacía: todo salvo la IA funciona; también se puede poner desde la página IA) |
| `KOS_SECRET_KEY` | clave maestra para guardar cifrada la clave de Anthropic desde la web: `openssl rand -hex 32`. Guárdala también fuera del servidor: sin ella, la clave guardada en una copia restaurada no se puede leer (basta con volver a introducirla) |
| `KOS_AI_MONTHLY_BUDGET_USD` | límite mensual de gasto en IA |
| `KOS_CROSSREF_MAILTO` | opcional: correo de contacto para Crossref (añadir estudios por DOI; el VPS necesita salida a `api.crossref.org`) |
| `KOS_WORKER_MEM_LIMIT` | 4g (bájalo si el VPS va justo, a costa de PDFs grandes) |

`chmod 600 .env` — contiene secretos; nunca se sube a git.

## 2. Construcción y arranque

```bash
docker compose build          # la primera vez descarga PyTorch CPU y modelos; 10-20 min
docker compose up -d
docker compose ps             # db, api (healthy), worker, web, gateway en marcha; migrate "exited (0)"
docker compose run --rm migrate kos create-user tu_usuario   # contraseña de ≥ 12 caracteres
# Modelo de transcripción (audio/vídeo), una sola vez (~1,6 GB, volumen `whisper`):
docker compose run --rm -e HF_HUB_OFFLINE=0 worker kos download-whisper
```

El límite de subida es de 4 GB (`KOS_MAX_UPLOAD_MB=4000`, `KOS_GATEWAY_MAX_BODY=4100MB`), lo que cabe en una clase grabada de 2-3 h; el `max_size` del bloque de tu Caddy (§3) tiene que dejarlo pasar. Transcribir 1 h de audio lleva ~26 min con 4 núcleos (`KOS_WHISPER_THREADS`); el worker procesa un trabajo cada vez.

Comprobación local antes de exponerlo:

```bash
curl -s http://127.0.0.1:${KOS_HTTP_PORT:-3000}/api/health     # {"status":"ok"}
docker compose logs worker | tail                                 # "handlers: [... 'source.extract' ...]"
docker compose run --rm migrate kos ai-check                      # si pusiste la clave: una llamada mínima real
```

### Conectar Claude (MCP)

En la página **IA** → «Conectar Claude (MCP)» crea un token (o `docker compose run --rm migrate kos create-token "Claude Code" --propose`) y copia el comando que muestra. Dirección: `https://tu-dominio/api/mcp`, cabecera `Authorization: Bearer kos_…`.

## 3. Publicarlo con tu Caddy

Añade **un bloque nuevo** al Caddyfile (no modifiques los existentes):

```
conocimiento.tudominio.com {
    header Strict-Transport-Security "max-age=31536000"   # Caddy no lo envía por defecto
    request_body {
        max_size 4100MB
    }
    reverse_proxy 127.0.0.1:3000
}
```

Valida y recarga (sin reiniciar), según cómo esté instalado Caddy:

```bash
# Caddy como servicio del sistema
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
# Caddy en un contenedor
docker exec <contenedor_caddy> caddy validate --config /etc/caddy/Caddyfile \
  && docker exec <contenedor_caddy> caddy reload --config /etc/caddy/Caddyfile
```

Si tu Caddy corre **en un contenedor**, `127.0.0.1` es el propio contenedor: usa la IP del host en la red de Docker (p. ej. `172.17.0.1:3000`, y entonces publica el gateway en esa interfaz cambiando `ports` en `docker-compose.yml`) o une ambos a una red Docker común. El informe de `preflight.sh` dice cuál es tu caso.

Abre `https://conocimiento.tudominio.com` e inicia sesión.

## 4. Copias de seguridad

```bash
./scripts/backup.sh /ruta/de/copias          # db.dump + storage.tar.gz + originals-excluded.tsv + SHA256SUMS + info.txt
```

- **No guarda los archivos que subiste tú** (PDF, presentaciones, vídeos…): ya los tienes, y copiarlos cada día multiplicaría su tamaño por 14. Sí guarda todo lo que se sacó de ellos (texto, páginas, transcripciones, resúmenes, lecturas, tarjetas, progreso), que está en la base de datos, y los archivos que no tienes tú: el audio descargado de YouTube y las notas que guardan los agentes por MCP. Cada copia lista en `originals-excluded.tsv` qué dejó fuera; `KOS_BACKUP_ORIGINALS=all` los incluye.
- Si se pierden, **vuelve a subir el mismo archivo** desde la biblioteca: se reconoce por su contenido, vuelve a su sitio y todo lo que dependía de él (páginas citadas, «ver en el minuto…») funciona otra vez, sin procesarlo de nuevo. Tiene que ser el mismo archivo exacto, no uno exportado o recomprimido otra vez.

- Comprueba que el volcado se puede leer y que el archivo de ficheros no está corrupto antes de dar la copia por buena.
- Conserva las 14 más recientes (`KOS_BACKUP_KEEP`).
- No copia `.env` (contiene secretos): guárdalo aparte, en tu gestor de contraseñas.
- Programación diaria (cron del usuario que gestiona Docker):

```
17 3 * * * cd /ruta/a/myknowledge && ./scripts/backup.sh /ruta/de/copias >> /ruta/de/copias/backup.log 2>&1
```

Una copia en el mismo disco no protege de la pérdida del VPS: sincroniza `/ruta/de/copias` fuera del servidor (p. ej. `rclone` o `rsync` a otro sitio).

Cada copia correcta queda registrada en la aplicación (página **Sistema**) y `kos monitor` avisa si la última tiene más de 26 h.

## 4b. Monitorización y avisos

La página **Sistema** muestra: worker (y si tiene OCR, transcripción, modelo de voz…), cola de trabajos, fuentes atascadas, disco, última copia y gasto de IA del mes. `kos monitor` hace las mismas comprobaciones y **avisa solo cuando algo cambia** (y una vez al día mientras siga mal, más un aviso cuando se resuelve).

1. Elige dónde recibir los avisos y ponlo en `.env` como `KOS_ALERT_URL`:
   - **ntfy** (recomendado: notificaciones en el móvil, gratis): instala la app ntfy, suscríbete a un tema con un nombre difícil de adivinar y usa `https://ntfy.sh/<tu-tema>` (o tu propio servidor ntfy).
   - **Slack o Discord**: la URL de un *webhook* entrante.
2. `KOS_PUBLIC_URL=https://conocimiento.tudominio.com` para que los avisos lleven enlace.
3. Tareas programadas (cron del usuario que gestiona Docker):

```
*/15 * * * * cd /ruta/a/myknowledge && docker compose exec -T api kos monitor >/dev/null 2>&1
31 8 * * *   cd /ruta/a/myknowledge && docker compose exec -T api kos remind  >/dev/null 2>&1
```

La segunda envía cada mañana cuántas tarjetas tienes por repasar. Si se cae el VPS entero, `kos monitor` no puede avisar: para eso usa un vigilante externo gratuito (p. ej. Uptime Kuma en otra máquina o un servicio de *uptime*) contra `https://tu-dominio/api/health`.

## 5. Restaurar

```bash
./scripts/restore.sh /ruta/de/copias/kos-AAAAMMDD-HHMMSS
```

Verifica las sumas de comprobación (aborta si no coinciden), pide escribir `restore`, para la aplicación, sustituye la base de datos, añade los ficheros de la copia a los que ya haya (no borra ninguno: la copia no tiene tus originales) y vuelve a arrancar (las migraciones nuevas se aplican solas). Al terminar lista los originales que faltan en el servidor para que los vuelvas a subir (`kos missing-originals` hace lo mismo cuando quieras). Para probar una copia sin tocar producción, restáurala en otra máquina con el mismo procedimiento. Los scripts usan el proyecto de Docker Compose por defecto (el nombre de la carpeta); si arrancaste la aplicación con `docker compose -p <nombre>`, exporta `COMPOSE_PROJECT_NAME=<nombre>` antes de ejecutarlos.

## 6. Actualizar

```bash
./scripts/backup.sh /ruta/de/copias           # siempre antes de actualizar
git pull
docker compose build
docker compose up -d                          # 'migrate' aplica las migraciones antes de arrancar la API
docker compose ps && curl -s http://127.0.0.1:3000/api/health
```

Si algo falla: `git checkout <commit anterior>`, `docker compose build && docker compose up -d` y, si una migración ya cambió la base de datos, restaura la copia del paso 1.

Tras actualizar a la versión con **Referencias y Análisis** (migración 0019), busca las referencias en lo que ya tenías: `docker compose exec api kos scan-references` (o el botón «Buscar de nuevo en toda la biblioteca» en Referencias). El análisis cruzado de cada fuente ya extraída se lanza desde su página («Analizar de nuevo con la biblioteca»); las nuevas se analizan solas.

Tras actualizar, comprueba la página **Sistema** (el worker nuevo debe dar señales y listar sus capacidades). Una vez al mes, actualiza también **yt-dlp** (YouTube cambia a menudo): `docker compose build --no-cache worker && docker compose up -d worker`. Para revisar dependencias con vulnerabilidades conocidas: `cd web && npm audit --omit=dev` y `uvx pip-audit` (ver SECURITY.md).

## 6b. Actualización automática (cada 30 minutos)

Las dos opciones usan el mismo script, `scripts/auto-update.sh`, que hace siempre lo mismo:
1. Comprueba que no haya otra actualización en marcha, que el VPS esté en la rama que sigue y que nadie haya cambiado el código a mano. Si algo no cuadra, no toca nada y avisa.
2. Si hay commits nuevos (solo avance directo, nunca reescribe historia), hace **copia de seguridad** con `backup.sh`. Si la copia falla, no actualiza.
3. Actualiza, construye y arranca. Espera a que las migraciones terminen bien y la API esté sana (hasta 4 minutos).
4. Si la versión nueva no arranca bien, **vuelve a la anterior** y te avisa, indicando qué copia restaurar si una migración llegó a aplicarse (las migraciones no se deshacen solas). Esa versión fallida no se reintenta hasta que haya otra más nueva o se fuerce con `--force`.
5. Avisa por ntfy o Slack (`KOS_ALERT_URL` del `.env`) de cada actualización y de cada fallo, y lo registra en la página Sistema.

Se pueden usar las dos a la vez (nunca se pisan), pero con una basta.

### Opción 1: el propio VPS mira GitHub (recomendada)
Usa la deploy key de solo lectura que ya tiene el VPS. No hace falta dar a GitHub ningún acceso al servidor.

```bash
crontab -e     # el usuario que gestiona Docker
*/30 * * * * cd /ruta/a/knowledge-os && ./scripts/auto-update.sh >> $HOME/kos-auto-update.log 2>&1
```

Sigue la rama que esté activa en el VPS (`git branch --show-current`). Para desplegar solo commits cuya CI pasó en GitHub, crea un token *fine-grained* de **solo lectura** (permiso «Actions: read» en este repositorio) y ponlo en un archivo privado:

```bash
install -m 600 /dev/null ~/.config/kos-deploy.env
nano ~/.config/kos-deploy.env
#   KOS_DEPLOY_REQUIRE_CI=1
#   KOS_DEPLOY_GITHUB_TOKEN=github_pat_...
```

Para comprobarlo a mano: `./scripts/auto-update.sh` (o `--force` para reconstruir sin cambios). El registro queda en `~/kos-auto-update.log`.

### Opción 2: GitHub Actions entra por SSH
El flujo `.github/workflows/deploy.yml` despliega al pasar la CI en `main` y, además, cada 30 minutos como repaso; también se lanza a mano desde la pestaña Actions («Deploy» → «Run workflow»). Despliega solo commits con la CI en verde. **GitHub solo ejecuta estos flujos desde `main`**: hace falta que el código esté en `main` y que el VPS siga `main` (`git checkout main`).

1. En tu PC, crea una clave **solo para esto**:
   `ssh-keygen -t ed25519 -f kos_github_deploy -N "" -C "github deploy knowledge-os"`
2. En el VPS, añade la pública a `~/.ssh/authorized_keys` del usuario que gestiona Docker, **restringida al script** (así, aunque se filtrara, solo podría lanzar una actualización):
   `command="/ruta/a/knowledge-os/scripts/auto-update.sh --from-ssh",restrict ssh-ed25519 AAAA... github deploy knowledge-os`
3. Obtén la huella del servidor: `ssh-keyscan -p <puerto> <host>`.
4. En GitHub → repositorio → *Settings* → *Secrets and variables* → *Actions*, crea:
   `VPS_HOST`, `VPS_USER`, `VPS_PORT` (si no es 22), `VPS_SSH_KEY` (el contenido de la clave **privada**) y `VPS_KNOWN_HOSTS` (la salida del paso 3).
5. Borra la clave privada de tu PC; si hace falta otra, se genera de nuevo.

Sin esos secretos, el flujo termina sin hacer nada (con un aviso). Cuesta unos minutos de Actions al día (48 comprobaciones de pocos segundos).

## 7. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| Subidas de > 10 MB fallan | La petición no pasa por el `gateway` (Caddy apunta a otro puerto) o falta `request_body max_size` |
| 403 al guardar cambios | `KOS_APP_ORIGIN` no coincide exactamente con la URL del navegador |
| No se puede iniciar sesión tras activar HTTPS | `KOS_COOKIE_SECURE=false` en producción o al revés en local |
| Documentos «Pendiente de procesar» sin avanzar | `docker compose logs worker`; RAM insuficiente (OOM) o el worker no arrancó |
| Fuente en «Error» con «el worker se detuvo N veces durante este trabajo» | El worker se quedó sin memoria en cada intento (`journalctl -k \| grep -i oom`). Baja `KOS_DOCLING_BATCH_PAGES` (por defecto 20) o sube `KOS_WORKER_MEM_LIMIT`, `docker compose up -d worker` y vuelve a procesar la fuente |
| Algunas páginas con «extracción básica» (`basic_extraction_pages`) | Esa tanda de páginas falló en Docling (p. ej. sin memoria); el resto del PDF sí se convirtió con Docling |
| PDF marcado «extracción básica» | Docling falló con ese archivo (ver `extraction_info` en la API); el documento sigue siendo buscable |
| Búsqueda por significado no disponible | Falta el modelo en la imagen (reconstruir) |
| La IA no responde | `kos ai-check`; página «IA»: clave, presupuesto y últimas llamadas |
| Audio o vídeo «Error: modelo de transcripción no instalado» | Falta `kos download-whisper` (§2) |
| YouTube «bloquea la descarga desde este servidor» | YouTube no deja descargar desde IP de centros de datos: sube tú los subtítulos o el audio |
| Página **Sistema**: «Worker — ningún worker ha dado señales» | `docker compose ps worker` y `docker compose logs worker` |
| No llegan avisos | `KOS_ALERT_URL` en `.env`, `docker compose up -d api` y prueba `docker compose exec api kos monitor` |
