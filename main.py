from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.routes_v2 import router as router_v2
from backend.database import engine, Base
import backend.models

app = FastAPI(title="FishRoute Pro API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

app.include_router(router_v2)

@app.get("/")
async def root():
    return {"status": "FishRoute Pro API v2 funcionando"}