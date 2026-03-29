import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from backend.api.routes_v2 import router as router_v2
from backend.database import engine, Base
import backend.models

# Rate limiter (clave por IP)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FishRoute Pro API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS dinámico — en producción usa solo el dominio de Vercel
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:4200")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        ]
        for sql in migraciones:
            await conn.execute(__import__('sqlalchemy').text(sql))

app.include_router(router_v2)

@app.get("/")
async def root():
    return {"status": "FishRoute Pro API v2 funcionando"}