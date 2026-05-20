"""
Servicio de mareas y olas — Open-Meteo Marine API (gratuito, sin API key).
Retorna altura de ola, swell y corriente oceánica por hora para las próximas 24 h.
"""
import httpx
import logging

logger = logging.getLogger(__name__)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


async def obtener_mareas(lat: float, lon: float) -> dict:
    """
    Consulta Open-Meteo Marine para un punto GPS.
    Retorna horas, resumen de pico y mejor ventana de salida.
    """
    params = {
        "latitude":     lat,
        "longitude":    lon,
        "hourly":       "wave_height,swell_wave_height,ocean_current_velocity",
        "forecast_days": 1,
        "timezone":     "America/Lima",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(MARINE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo Marine no disponible: %s", exc)
        return {"disponible": False, "horas": [], "resumen": {}}

    hourly   = data.get("hourly", {})
    tiempos  = hourly.get("time", [])
    olas     = hourly.get("wave_height", [])
    swell    = hourly.get("swell_wave_height", [])
    corriente = hourly.get("ocean_current_velocity", [])

    horas = []
    for i, t in enumerate(tiempos[:24]):
        horas.append({
            "hora":         t,
            "olas_m":       round(olas[i], 2)     if i < len(olas)      and olas[i]      is not None else None,
            "swell_m":      round(swell[i], 2)    if i < len(swell)     and swell[i]     is not None else None,
            "corriente_ms": round(corriente[i], 2) if i < len(corriente) and corriente[i] is not None else None,
        })

    olas_vals = [h["olas_m"] for h in horas if h["olas_m"] is not None]
    min_ola   = min(olas_vals) if olas_vals else None
    max_ola   = max(olas_vals) if olas_vals else None

    mejor_hora = None
    if olas_vals:
        idx_min    = olas_vals.index(min_ola)
        mejor_hora = horas[idx_min]["hora"] if idx_min < len(horas) else None

    return {
        "disponible": True,
        "horas":      horas,
        "resumen": {
            "max_ola_m":  max_ola,
            "min_ola_m":  min_ola,
            "mejor_hora": mejor_hora,
            "descripcion": (
                f"Pico de {max_ola} m — mejor ventana aprox. {mejor_hora[11:16] if mejor_hora else '—'}"
                if max_ola is not None else "Datos no disponibles"
            ),
        },
    }
