import httpx
from datetime import datetime

# Open-Meteo Marine API — sin API key, completamente gratuita
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

async def obtener_condiciones_mar(lat: float, lon: float) -> dict:
    from backend.cache.redis_cache import cache_get, cache_set, make_key, TTL_CLIMA

    key = make_key("clima", lat, lon)
    cached = cache_get(key)
    if cached:
        cached["_from_cache"] = True
        return cached

    params_mar = {
        "latitude": lat, "longitude": lon,
        "current": ["wave_height", "wave_period", "wave_direction", "wind_wave_height"],
        "forecast_days": 1, "timezone": "America/Lima"
    }
    params_viento = {
        "latitude": lat, "longitude": lon,
        "current": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "weather_code"],
        "forecast_days": 1, "timezone": "America/Lima"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp_mar, resp_viento = await __import__('asyncio').gather(
            client.get(MARINE_URL, params=params_mar),
            client.get(WEATHER_URL, params=params_viento),
        )
        resp_mar.raise_for_status()
        resp_viento.raise_for_status()
        datos_mar    = resp_mar.json()
        datos_viento = resp_viento.json()

    current_mar    = datos_mar.get("current", {})
    current_viento = datos_viento.get("current", {})
    altura_olas    = current_mar.get("wave_height", 0)
    vel_viento     = current_viento.get("wind_speed_10m", 0)
    rafagas        = current_viento.get("wind_gusts_10m", 0)
    alerta         = _calcular_alerta(altura_olas, vel_viento)

    resultado = {
        "latitud": lat, "longitud": lon,
        "timestamp": datetime.now().isoformat(),
        "mar": {
            "altura_olas_m":  round(altura_olas, 2),
            "periodo_olas_s": round(current_mar.get("wave_period", 0), 1),
            "direccion_olas": round(current_mar.get("wave_direction", 0), 0),
        },
        "viento": {
            "velocidad_kmh":    round(vel_viento, 1),
            "rafagas_kmh":      round(rafagas, 1),
            "direccion_grados": round(current_viento.get("wind_direction_10m", 0), 0),
        },
        "alerta": alerta,
        "navegacion_segura": alerta["nivel"] in ("VERDE", "AMARILLO"),
        "_from_cache": False,
    }
    cache_set(key, resultado, TTL_CLIMA)
    return resultado

async def obtener_pronostico_48h(lat: float, lon: float) -> dict:
    """
    Pronóstico horario de olas y viento para las próximas 48 horas.
    Incluye recomendación de mejor ventana para salir al mar.
    """
    from backend.cache.redis_cache import cache_get, cache_set, make_key
    TTL_PRONOSTICO = 3600  # 1 hora

    key = make_key("pronostico48h", lat, lon)
    cached = cache_get(key)
    if cached:
        return cached

    params_mar = {
        "latitude": lat, "longitude": lon,
        "hourly": ["wave_height", "wave_period"],
        "forecast_days": 2, "timezone": "America/Lima"
    }
    params_viento = {
        "latitude": lat, "longitude": lon,
        "hourly": ["wind_speed_10m", "wind_gusts_10m"],
        "forecast_days": 2, "timezone": "America/Lima"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp_mar, resp_viento = await __import__('asyncio').gather(
            client.get(MARINE_URL, params=params_mar),
            client.get(WEATHER_URL, params=params_viento),
        )
        resp_mar.raise_for_status()
        resp_viento.raise_for_status()
        d_mar    = resp_mar.json().get("hourly", {})
        d_viento = resp_viento.json().get("hourly", {})

    tiempos  = d_mar.get("time", [])
    olas     = d_mar.get("wave_height", [])
    periodo  = d_mar.get("wave_period", [])
    viento   = d_viento.get("wind_speed_10m", [])
    rafagas  = d_viento.get("wind_gusts_10m", [])

    horas = []
    mejor_ventana = None

    for i, t in enumerate(tiempos[:48]):
        h_olas   = olas[i]   if i < len(olas)    else 0
        h_viento = viento[i] if i < len(viento)  else 0
        h_rafaga = rafagas[i] if i < len(rafagas) else 0
        h_periodo = periodo[i] if i < len(periodo) else 0
        alerta   = _calcular_alerta(h_olas, h_viento)

        horas.append({
            "hora":       t,
            "olas_m":     round(h_olas, 2),
            "periodo_s":  round(h_periodo, 1),
            "viento_kmh": round(h_viento, 1),
            "rafagas_kmh": round(h_rafaga, 1),
            "nivel":      alerta["nivel"],
            "color":      alerta["color"],
        })

        # Detectar primera ventana VERDE de al menos 6h consecutivas
        if mejor_ventana is None and alerta["nivel"] == "VERDE":
            ventana_verde = sum(
                1 for j in range(i, min(i + 6, len(tiempos)))
                if _calcular_alerta(
                    olas[j] if j < len(olas) else 0,
                    viento[j] if j < len(viento) else 0
                )["nivel"] == "VERDE"
            )
            if ventana_verde >= 6:
                mejor_ventana = t

    resultado = {
        "lat": lat, "lon": lon,
        "horas": horas,
        "mejor_ventana_salida": mejor_ventana,
        "resumen": (
            f"Mejor momento para salir: {mejor_ventana}"
            if mejor_ventana else "Sin ventana favorable en las próximas 48h"
        )
    }
    cache_set(key, resultado, TTL_PRONOSTICO)
    return resultado


def _calcular_alerta(altura_olas: float, vel_viento: float) -> dict:
    """Semáforo de seguridad para navegación."""
    if altura_olas >= 3.0 or vel_viento >= 60:
        return {
            "nivel": "ROJO",
            "mensaje": "Condiciones peligrosas — no salir al mar",
            "color": "#E24B4A"
        }
    elif altura_olas >= 1.5 or vel_viento >= 40:
        return {
            "nivel": "AMARILLO",
            "mensaje": "Condiciones moderadas — navegar con precaución",
            "color": "#EF9F27"
        }
    else:
        return {
            "nivel": "VERDE",
            "mensaje": "Condiciones favorables para la pesca",
            "color": "#1D9E75"
        }
