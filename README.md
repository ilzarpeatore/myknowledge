# Knowledge OS

Sistema personal de aprendizaje y gestión del conocimiento: convierte cursos, libros, documentos y clases en conocimiento organizado, conectado y trazable hasta su fuente original.

Dominio inicial: ciencias del entrenamiento y del ejercicio.

## Estado
- Fase 0 (auditoría y planificación): completada.
- Fase 1: biblioteca, extracción con Docling + OCR, fragmentos con página exacta, búsqueda por palabras y por significado (modelo local), capa de IA lista para Anthropic (falta la clave), modelo de conocimiento con revisión humana.
- Fase 3 (Learning Center): formaciones, cursos, módulos, lecciones, materiales y progreso.
- Fase 8 (adelantada): guía de despliegue, copias de seguridad y restauración.
- Pendiente: extracción automática de conceptos con IA, grafo y mapa mental (Fase 2), validación con material real, primer despliegue.

Documentación:
- [docs/REPOSITORY_AUDIT.md](docs/REPOSITORY_AUDIT.md) — auditoría de `ddsyasas/llm-wiki` y `lucasastorian/llmwiki`
- [docs/RESEARCH_OTHER_REPOS.md](docs/RESEARCH_OTHER_REPOS.md) — otros repositorios evaluados
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — despliegue, copias de seguridad y restauración
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/DATA_MODEL.md](docs/DATA_MODEL.md) · [docs/TECHNICAL_DECISIONS.md](docs/TECHNICAL_DECISIONS.md) · [docs/ROADMAP.md](docs/ROADMAP.md)

## Estructura
```
backend/   Python (FastAPI + SQLAlchemy + Alembic): API, worker y CLI de administración
  kos/api       rutas HTTP
  kos/auth      contraseñas (argon2), sesiones de servidor, límite de intentos
  kos/jobs      cola de trabajos en PostgreSQL y worker
  kos/models    modelos de datos
  migrations/   migraciones de base de datos
  tests/        tests (necesitan PostgreSQL con pgvector)
web/       Next.js (interfaz)
gateway/   Caddy interno: /api/* → API, resto → web (las subidas grandes no pasan por Next.js)
scripts/   preflight.sh (inventario del servidor, solo lectura), backup.sh, restore.sh
```

## Despliegue con Docker (VPS)
Guía completa, con inventario previo de solo lectura, copias de seguridad, restauración y actualización: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**. Resumen:

Requisitos: Docker con Compose y **al menos 4 GB de RAM libres** para el procesador de documentos (Docling usa ~2 GB por documento). La primera construcción descarga PyTorch (CPU) y los modelos de Docling (~2,5 GB en total).

```bash
cp .env.example .env          # rellena KOS_DB_PASSWORD y KOS_APP_ORIGIN
docker compose up -d --build  # db, migrate, api, worker, web, gateway
docker compose run --rm migrate kos create-user <usuario>   # pide la contraseña (mín. 12 caracteres)
```
Todo el sistema entra por una única puerta (`gateway`, un Caddy interno) que escucha solo en `127.0.0.1:${KOS_HTTP_PORT}`: envía `/api/*` a la API y el resto a la web. Ejemplo de bloque para el Caddy del VPS:
```
knowledge.example.com {
    reverse_proxy 127.0.0.1:3000
}
```
`KOS_APP_ORIGIN` debe coincidir exactamente con la URL pública (`https://knowledge.example.com`): las peticiones que modifican datos desde otro origen se rechazan.

Cambiar la contraseña (cierra todas las sesiones):
```bash
docker compose run --rm migrate kos reset-password <usuario>
```

## Desarrollo local
Requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 16 con la extensión pgvector.

```bash
# Backend
cd backend
uv sync
export KOS_DATABASE_URL=postgresql+psycopg://kos:kos@localhost:5432/kos_dev
export KOS_COOKIE_SECURE=false KOS_APP_ORIGIN=http://localhost:3000
uv run kos migrate
uv run kos create-user <usuario>
uv run uvicorn kos.api.main:app --reload --port 8000   # API
uv run kos worker                                      # worker (otra terminal)

# Frontend
cd web && npm install && npm run dev                   # http://localhost:3000
# Nota: en `npm run dev` las subidas pasan por el proxy de Next.js, limitado a 10 MB.
# Para archivos grandes usa Docker (gateway) o sube directamente a la API.
```

Tests del backend (usan una base de datos desechable; la vacían y recrean):
```bash
cd backend
KOS_TEST_DATABASE_URL=postgresql+psycopg://kos:kos@localhost:5432/kos_test uv run pytest
uv run ruff check . && uv run ruff format --check .
```
