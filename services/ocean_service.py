import httpx
import numpy as np
from datetime import datetime, timedelta
from backend.cache.redis_cache import cache_get, cache_set, make_key, TTL_OCEAN
ERDDAP_BASE  = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
ERDDAP_BASE2 = "https://coastwatch.noaa.gov/erddap/griddap"

LAT_MIN, LAT_MAX = -18.0, -4.0
LON_MIN, LON_MAX = -82.0, -70.0

TEMP_OPTIMA = {
    "ANCHOVETA": {"min": 14.0, "max": 20.0, "ideal": 17.0},
    "BONITO":    {"min": 18.0, "max": 24.0, "ideal": 21.0},
    "CABALLA":   {"min": 15.0, "max": 22.0, "ideal": 18.5},
    "JUREL":     {"min": 14.0, "max": 21.0, "ideal": 17.5},
}

async def obtener_temperatura_mar(lat: float, lon: float) -> dict:
    # Buscar en cache zonas vecinas (radio de 0.5°)
    for dlat, dlon in [(0,0), (0.5,0), (-0.5,0), (0,0.5), (0,-0.5)]:
        key_vecino = make_key("sst", lat + dlat, lon + dlon)
        cached = cache_get(key_vecino)
        if cached and cached.get("disponible"):
            return cached

    key = make_key("sst", lat, lon)
    cached = cache_get(key)
    if cached:
        return cached

    delta = 0.1
    lat1, lat2 = lat - delta, lat + delta
    lon1, lon2 = lon - delta, lon + delta
    fecha = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT09:00:00Z")

    url = (
        f"{ERDDAP_BASE}/jplMURSST41.json"
        f"?analysed_sst[({fecha})]"
        f"[({lat1:.2f}):1:({lat2:.2f})]"
        f"[({lon1:.2f}):1:({lon2:.2f})]"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        valores = [row[3] for row in data["table"]["rows"] if row[3] is not None]
        if not valores:
            return {"temperatura_c": None, "fuente": "jplMURSST41", "disponible": False}

        resultado = {
            "temperatura_c": round(float(np.mean(valores)), 2),
            "fuente": "NASA MUR SST / ERDDAP",
            "disponible": True,
            "fecha_dato": fecha[:10],
        }
        cache_set(key, resultado, TTL_OCEAN)
        return resultado

    except Exception as e:
        return {"temperatura_c": None, "fuente": "jplMURSST41",
                "disponible": False, "error": str(e)}

async def obtener_clorofila(lat: float, lon: float) -> dict:

    key = make_key("chla", lat, lon)
    cached = cache_get(key)
    if cached:
        return cached

    delta = 0.2
    lat1, lat2 = lat - delta, lat + delta
    lon1, lon2 = lon - delta, lon + delta

    async with httpx.AsyncClient(timeout=25.0) as client:
        for dias_atras in [5, 13, 21, 29, 37]:
            fecha = (datetime.utcnow() - timedelta(days=dias_atras)).strftime("%Y-%m-%dT00:00:00Z")
            url = (
                f"{ERDDAP_BASE}/erdMBchla8day_LonPM180.json"
                f"?chlorophyll[({fecha})][(0)]"
                f"[({lat1:.2f}):1:({lat2:.2f})]"
                f"[({lon1:.2f}):1:({lon2:.2f})]"
            )
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                valores = [
                    row[4] for row in data["table"]["rows"]
                    if row[4] is not None and row[4] > 0
                ]
                if not valores:
                    continue

                chla = round(float(np.mean(valores)), 3)
                nivel, descripcion = _clasificar_clorofila(chla)
                resultado = {
                    "clorofila_mg_m3": chla,
                    "nivel": nivel,
                    "descripcion": descripcion,
                    "fuente": "MODIS Aqua / ERDDAP NOAA",
                    "disponible": True,
                    "fecha_dato": fecha[:10],
                }
                cache_set(key, resultado, TTL_OCEAN)
                return resultado
            except Exception:
                continue

    return {
        "clorofila_mg_m3": None,
        "nivel": "SIN_DATOS",
        "disponible": False,
        "error": "No se encontraron datos recientes de clorofila"
    }
def _clasificar_clorofila(chla: float) -> tuple:
    if chla >= 5.0:
        return ("MUY_ALTA", "Zona muy productiva — alta probabilidad de cardúmenes")
    elif chla >= 2.0:
        return ("ALTA", "Zona productiva — buena probabilidad de pesca")
    elif chla >= 0.5:
        return ("MEDIA", "Zona con actividad moderada")
    else:
        return ("BAJA", "Zona poco productiva")


def calcular_score_temperatura(temp_c: float, especie: str) -> float:
    """
    Retorna 0-100 según qué tan cercana está la temperatura al óptimo de la especie.
    """
    if temp_c is None:
        return 50.0  # valor neutro si no hay dato

    rango = TEMP_OPTIMA.get(especie.upper(), TEMP_OPTIMA["ANCHOVETA"])

    if temp_c < rango["min"] or temp_c > rango["max"]:
        return 0.0  # fuera del rango viable

    distancia = abs(temp_c - rango["ideal"])
    rango_mitad = (rango["max"] - rango["min"]) / 2
    score = max(0.0, 100.0 * (1 - distancia / rango_mitad))
    return round(score, 1)


def calcular_score_clorofila(chla: float) -> float:
    """
    Retorna 0-100 según concentración de clorofila-a.
    Escala logarítmica: diferencia entre 0.1 y 1.0 es más importante que entre 5 y 6.
    """
    if chla is None or chla <= 0:
        return 0.0
    import math
    score = min(100.0, 25.0 * math.log10(1 + chla * 10))
    return round(score, 1)


async def obtener_datos_zona(lat: float, lon: float, especie: str = "ANCHOVETA") -> dict:
    """
    Función principal: retorna todos los datos oceanográficos de un punto
    más los scores calculados para el algoritmo FishScore.
    """
    import asyncio
    sst, chla = await asyncio.gather(
        obtener_temperatura_mar(lat, lon),
        obtener_clorofila(lat, lon),
    )

    score_temp = calcular_score_temperatura(sst.get("temperatura_c"), especie)
    score_chla = calcular_score_clorofila(chla.get("clorofila_mg_m3"))

    return {
        "latitud": lat,
        "longitud": lon,
        "especie": especie,
        "temperatura": sst,
        "clorofila": chla,
        "scores": {
            "temperatura": score_temp,
            "clorofila": score_chla,
        }
    }