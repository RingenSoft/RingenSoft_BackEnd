import asyncio
import numpy as np
from services.ocean_service import (
    obtener_temperatura_mar,
    obtener_clorofila,
    calcular_score_temperatura,
    calcular_score_clorofila,
)
from services.weather_service import obtener_condiciones_mar

# Cuántos puntos consultar en paralelo (no saturar las APIs)
BATCH_SIZE = 8


async def calcular_fish_score_punto(
    punto: dict,
    especie: str,
    clima_cache: dict = None
) -> dict:
    """
    Calcula FishScore 0-100 para un punto usando datos reales de satélite.
    Formula:
      FishScore = Chla(35%) + SST(25%) + Clima(20%) + Distancia(10%) + Profundidad(10%)
    """
    lat, lon = punto["lat"], punto["lon"]

    # Clima: reutiliza cache si ya lo tenemos (mismo área general)
    if clima_cache:
        clima = clima_cache
    else:
        clima = await obtener_condiciones_mar(lat, lon)

    # Oceanografía: SST y Clorofila en paralelo
    sst, chla = await asyncio.gather(
        obtener_temperatura_mar(lat, lon),
        obtener_clorofila(lat, lon),
    )

    # Calcular scores individuales
    score_chla  = calcular_score_clorofila(chla.get("clorofila_mg_m3"))
    score_temp  = calcular_score_temperatura(sst.get("temperatura_c"), especie)
    score_clima = _score_clima(clima)
    score_dist  = _score_distancia(punto.get("distancia_km", 999))

    # FishScore ponderado
    fish_score = round(
        score_chla  * 0.35 +
        score_temp  * 0.25 +
        score_clima * 0.20 +
        score_dist  * 0.20,
        1
    )

    return {
        **punto,
        "fish_score":   fish_score,
        "score_chla":   score_chla,
        "score_temp":   score_temp,
        "score_clima":  score_clima,
        "temperatura_c": sst.get("temperatura_c"),
        "clorofila":    chla.get("clorofila_mg_m3"),
        "nivel_chla":   chla.get("nivel", "SIN_DATOS"),
        "navegacion_segura": clima.get("navegacion_segura", True),
    }


async def calcular_scores_zona(
    puntos: list[dict],
    especie: str,
    lat_centro: float,
    lon_centro: float,
) -> list[dict]:
    """
    Calcula FishScore para una lista de puntos en batches paralelos.
    Retorna lista ordenada de mayor a menor FishScore.
    """
    # Un solo fetch de clima para el centro de la zona (ahorra llamadas API)
    clima_base = await obtener_condiciones_mar(lat_centro, lon_centro)

    resultados = []
    total = len(puntos)

    for i in range(0, total, BATCH_SIZE):
        batch = puntos[i:i + BATCH_SIZE]
        tareas = [
            calcular_fish_score_punto(p, especie, clima_base)
            for p in batch
        ]
        batch_result = await asyncio.gather(*tareas, return_exceptions=True)

        for r in batch_result:
            if isinstance(r, Exception):
                continue  # skip puntos con error de API
            resultados.append(r)

        # Pausa breve entre batches para no saturar ERDDAP
        if i + BATCH_SIZE < total:
            await asyncio.sleep(0.5)

    # Ordenar por FishScore descendente
    resultados.sort(key=lambda x: x["fish_score"], reverse=True)
    return resultados


def _score_clima(clima: dict) -> float:
    """
    Score de clima 0-100.
    Penaliza olas altas y viento fuerte.
    """
    olas  = clima.get("mar", {}).get("altura_olas_m", 0)
    viento = clima.get("viento", {}).get("velocidad_kmh", 0)

    if olas >= 3.0 or viento >= 60:
        return 0.0
    elif olas >= 1.5 or viento >= 40:
        score = 50.0
    else:
        score = 100.0

    # Penalización progresiva por olas
    score -= min(40.0, olas * 15)
    return round(max(0.0, score), 1)


def _score_distancia(distancia_km: float) -> float:
    """
    Score de distancia 0-100.
    Prefiere zonas más cercanas para ahorrar combustible.
    Óptimo: < 50km. Máximo útil: 200km.
    """
    if distancia_km <= 50:
        return 100.0
    elif distancia_km >= 200:
        return 10.0
    else:
        return round(100.0 - (distancia_km - 50) * (90.0 / 150.0), 1)


def seleccionar_mejores_zonas(
    puntos_scored: list[dict],
    top_n: int = 5,
    score_minimo: float = 30.0
) -> list[dict]:
    """
    Filtra y retorna las mejores zonas para incluir en la ruta.
    Solo zonas con FishScore >= score_minimo y navegación segura.
    """
    candidatos = [
        p for p in puntos_scored
        if p["fish_score"] >= score_minimo
        and p.get("navegacion_segura", True)
    ]
    return candidatos[:top_n]