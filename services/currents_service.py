"""
Servicio de corrientes oceánicas superficiales — NOAA OSCAR via ERDDAP.

Provee componentes u (este-oeste) y v (norte-sur) en m/s.
Fuente: erdTAgeo1day_LonPM180 (OSCAR 1° resolución, 1 día de retraso)
"""
import math
import asyncio
from datetime import datetime, timedelta
import httpx
from backend.cache.redis_cache import cache_get, cache_set, make_key, TTL_OCEAN

ERDDAP_BASE = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdTAgeo1day_LonPM180.json"

async def obtener_corriente(lat: float, lon: float) -> dict:
    """
    Retorna las componentes de corriente superficial en (lat, lon).
    u = componente este-oeste (m/s, positivo hacia el este)
    v = componente norte-sur  (m/s, positivo hacia el norte)
    """
    cache_key = make_key("corriente", lat, lon, "oscar")
    cached = await cache_get(cache_key)
    if cached:
        return cached

    # OSCAR tiene ~1 día de retraso
    fecha = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
    lat_r = round(lat)    # resolución 1°
    lon_r = round(lon)

    url = (
        f"{ERDDAP_BASE}"
        f"?u[({fecha}):1:({fecha})][({lat_r}):1:({lat_r})][({lon_r}):1:({lon_r})],"
        f"v[({fecha}):1:({fecha})][({lat_r}):1:({lat_r})][({lon_r}):1:({lon_r})]"
    )

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        rows = data.get("table", {}).get("rows", [])
        if not rows:
            return _corriente_nula()

        u = float(rows[0][3]) if rows[0][3] is not None else 0.0
        v = float(rows[0][4]) if rows[0][4] is not None else 0.0

        # Reemplazar NaN
        if math.isnan(u): u = 0.0
        if math.isnan(v): v = 0.0

        resultado = {"u": round(u, 4), "v": round(v, 4)}
        await cache_set(cache_key, resultado, TTL_OCEAN)
        return resultado

    except Exception:
        # Sin corrientes disponibles → no bloquear la app
        return _corriente_nula()


def ajustar_velocidad_por_corriente(
    vel_kmh: float,
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    u: float, v: float,
) -> float:
    """
    Calcula la velocidad efectiva del barco considerando la corriente marina.
    - Si la corriente va a favor del rumbo, el barco llega más rápido (ahorra combustible).
    - Si va en contra, consume más y tarda más.
    Retorna la velocidad efectiva sobre el fondo (SOG) en km/h.
    """
    # Rumbo del tramo (radianes)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    bearing = math.atan2(dlon, dlat)  # referencia norte = 0

    # Proyección de la corriente sobre el rumbo del barco
    # u → este-oeste, v → norte-sur
    corriente_proyectada_ms = u * math.sin(bearing) + v * math.cos(bearing)
    corriente_proyectada_kmh = corriente_proyectada_ms * 3.6

    vel_efectiva = vel_kmh + corriente_proyectada_kmh
    return max(1.0, vel_efectiva)   # mínimo 1 km/h


def _corriente_nula() -> dict:
    return {"u": 0.0, "v": 0.0}
