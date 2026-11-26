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
openssl rand -base64 32      # pega el resultado en KOS_DB_PASSWORD
nano .env
```

| Variable | Valor |
|---|---|
| `KOS_DB_PASSWORD` | la generada arriba |
| `KOS_APP_ORIGIN` | `https://conocimiento.tudominio.com` (exacta, con `https://`) |
| `KOS_COOKIE_SECURE` | `true` |
| `KOS_HTTP_PORT` | 3000 u otro puerto libre |
| `ANTHROPIC_API_KEY` | tu clave (puede quedar vacía: todo salvo la IA funciona) |
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

Para subir clases grabadas, sube el límite en `.env` (`KOS_MAX_UPLOAD_MB=4000`, `KOS_GATEWAY_MAX_BODY=4100MB`) y el `max_size` del bloque de tu Caddy (§3). Transcribir 1 h de audio lleva ~26 min con 4 núcleos (`KOS_WHISPER_THREADS`); el worker procesa un trabajo cada vez.

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
    request_body {
        max_size 250MB
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
./scripts/backup.sh /ruta/de/copias          # db.dump + storage.tar.gz + SHA256SUMS + info.txt
```

- Comprueba que el volcado se puede leer y que el archivo de ficheros no está corrupto antes de dar la copia por buena.
- Conserva las 14 más recientes (`KOS_BACKUP_KEEP`).
- No copia `.env` (contiene secretos): guárdalo aparte, en tu gestor de contraseñas.
- Programación diaria (cron del usuario que gestiona Docker):

```
17 3 * * * cd /ruta/a/myknowledge && ./scripts/backup.sh /ruta/de/copias >> /ruta/de/copias/backup.log 2>&1
```

Una copia en el mismo disco no protege de la pérdida del VPS: sincroniza `/ruta/de/copias` fuera del servidor (p. ej. `rclone` o `rsync` a otro sitio).

## 5. Restaurar

```bash
./scripts/restore.sh /ruta/de/copias/kos-AAAAMMDD-HHMMSS
```

Verifica las sumas de comprobación (aborta si no coinciden), pide escribir `restore`, para la aplicación, sustituye la base de datos y los ficheros y vuelve a arrancar (las migraciones nuevas se aplican solas). Para probar una copia sin tocar producción, restáurala en otra máquina con el mismo procedimiento.

## 6. Actualizar

```bash
./scripts/backup.sh /ruta/de/copias           # siempre antes de actualizar
git pull
docker compose build
docker compose up -d                          # 'migrate' aplica las migraciones antes de arrancar la API
docker compose ps && curl -s http://127.0.0.1:3000/api/health
```

Si algo falla: `git checkout <commit anterior>`, `docker compose build && docker compose up -d` y, si una migración ya cambió la base de datos, restaura la copia del paso 1.

## 7. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| Subidas de > 10 MB fallan | La petición no pasa por el `gateway` (Caddy apunta a otro puerto) o falta `request_body max_size` |
| 403 al guardar cambios | `KOS_APP_ORIGIN` no coincide exactamente con la URL del navegador |
| No se puede iniciar sesión tras activar HTTPS | `KOS_COOKIE_SECURE=false` en producción o al revés en local |
| Documentos «Pendiente de procesar» sin avanzar | `docker compose logs worker`; RAM insuficiente (OOM) o el worker no arrancó |
| PDF marcado «extracción básica» | Docling falló con ese archivo (ver `extraction_info` en la API); el documento sigue siendo buscable |
| Búsqueda por significado no disponible | Falta el modelo en la imagen (reconstruir) |
| La IA no responde | `kos ai-check`; página «IA»: clave, presupuesto y últimas llamadas |
