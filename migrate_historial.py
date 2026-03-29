"""
Migración: agrega columnas ML a historial_rutas.
Ejecutar una sola vez desde la raíz del proyecto:
    python backend/migrate_historial.py
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:root@localhost:5432/fishroute_db"
)

COLUMNAS = [
    ("fish_score_promedio", "FLOAT"),
    ("zonas_visitadas_num", "INTEGER"),
    ("tiempo_total_horas",  "FLOAT"),
    ("clorofila_promedio",  "FLOAT"),
    ("ruta_json",           "JSONB"),
]

async def migrar():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for col, tipo in COLUMNAS:
            try:
                await conn.execute(text(
                    f"ALTER TABLE historial_rutas ADD COLUMN IF NOT EXISTS {col} {tipo}"
                ))
                print(f"  ✓ Columna '{col}' agregada ({tipo})")
            except Exception as e:
                print(f"  ✗ Error en '{col}': {e}")
    await engine.dispose()
    print("Migración completada.")

if __name__ == "__main__":
    asyncio.run(migrar())
