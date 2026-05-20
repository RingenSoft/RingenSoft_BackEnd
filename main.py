import os
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from api.routes_v2 import router as router_v2
from database import engine, Base
import models

# Directorio para fotos subidas por usuarios
UPLOADS_DIR = Path("uploads/avistamientos")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# Rate limiter (clave por IP)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FishRoute Pro API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS dinámico — en producción usa solo el dominio de Vercel
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:4200")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migración: agregar columnas nuevas a tablas existentes si no existen
        migraciones = [
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS zona_habitual VARCHAR(20)",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tipo_pescador VARCHAR(50)",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS anos_experiencia INTEGER",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS licencia_pesca VARCHAR(100)",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS telefono VARCHAR(20)",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS onboarding_completo BOOLEAN DEFAULT FALSE",
            "ALTER TABLE avistamientos ADD COLUMN IF NOT EXISTS foto_url VARCHAR(500)",
            # BitacoraCapturas
            "ALTER TABLE bitacora_capturas ADD COLUMN IF NOT EXISTS notas VARCHAR(500)",
            # Índices para queries frecuentes
            "CREATE INDEX IF NOT EXISTS idx_historial_usuario ON historial_rutas (id_embarcacion)",
            "CREATE INDEX IF NOT EXISTS idx_historial_fecha ON historial_rutas (fecha_calculo DESC)",
            "CREATE INDEX IF NOT EXISTS idx_avistamientos_fecha ON avistamientos (fecha DESC)",
            "CREATE INDEX IF NOT EXISTS idx_mensajes_fecha ON mensajes_comunidad (fecha DESC)",
            "CREATE INDEX IF NOT EXISTS idx_bitacora_usuario ON bitacora_capturas (id_usuario)",
            "CREATE INDEX IF NOT EXISTS idx_bitacora_fecha ON bitacora_capturas (fecha DESC)",
        ]
        for sql in migraciones:
            try:
                await conn.execute(__import__('sqlalchemy').text(sql))
            except Exception:
                pass  # Columna/índice ya existe — continuar

app.include_router(router_v2)

# Servir archivos estáticos de uploads (fotos de avistamientos)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/")
async def root():
    return {"status": "FishRoute Pro API v2 funcionando"}