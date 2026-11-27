"""Time the main operations on the synthetic large library (in-process ASGI)."""
import asyncio, statistics, sys, time, uuid
sys.path.insert(0, ".")  # run from backend/
import httpx
from sqlalchemy import select
from kos.api.main import create_app
from kos.auth.service import create_single_user
from kos.db import get_sessionmaker
from kos.models import Entity, QueryRecord

async def main():
    sm = get_sessionmaker()
    async with sm() as db:
        try:
            await create_single_user(db, "perf", "perf-password-123")
        except Exception:
            pass
        e = await db.scalar(select(Entity).where(Entity.slug == "prueba-concepto-15"))
    t = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=t, base_url="http://testserver", timeout=300) as c:
        await c.post("/api/auth/login", json={"username": "perf", "password": "perf-password-123"})
        c.headers["origin"] = "http://testserver"
        cases = [
            ("Buscar por palabras", "/api/search?q=fatiga+muscular+reserva&mode=keyword"),
            ("Buscar por significado", "/api/search?q=cómo+afecta+el+cansancio+a+la+fuerza&mode=semantic"),
            ("Buscar combinada", "/api/search?q=volumen+semanal+hipertrofia&mode=hybrid"),
            ("Lista de fuentes", "/api/sources?limit=50"),
            ("Conceptos (filtro)", "/api/entities?q=concepto%2015"),
            ("Ficha de concepto", f"/api/entities/{e.slug}"),
            ("Grafo (vecindario)", f"/api/graph/neighbourhood?entity={e.id}&depth=2"),
            ("Comparación", f"/api/entities/{e.id}/comparison"),
            ("Tarjetas por repasar", "/api/study/due?limit=50"),
            ("Informe de lagunas (2000 conceptos)", "/api/study/gaps"),
            ("Estado del sistema", "/api/system/health"),
        ]
        rows = []
        for label, url in cases:
            times = []
            for i in range(6):
                t0 = time.perf_counter(); r = await c.get(url); dt = time.perf_counter() - t0
                assert r.status_code == 200, (url, r.status_code, r.text[:200])
                if i: times.append(dt * 1000)  # first call warms caches/model
            rows.append((label, statistics.median(times), max(times)))
        # context building of a question (everything except the AI call)
        from kos.ai import query as aq
        async with sm() as db:
            q = QueryRecord(question="¿Qué he aprendido sobre prueba concepto 15 y la fatiga?", scope_kind="global", mode="research")
            db.add(q); await db.flush()
            times = []
            for i in range(4):
                t0 = time.perf_counter(); ctx, _ = await aq.build_context(db, q); dt = time.perf_counter() - t0
                if i: times.append(dt * 1000)
            rows.append((f"Preparar pregunta a la IA ({len(ctx.fragments)} fragmentos)", statistics.median(times), max(times)))
            await db.rollback()
    print(f"{'operación':45} {'mediana ms':>11} {'máx ms':>9}")
    for label, med, mx in rows:
        print(f"{label:45} {med:11.0f} {mx:9.0f}")
asyncio.run(main())
