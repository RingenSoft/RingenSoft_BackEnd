import numpy as np
from backend.services.vessel_model import (
    calcular_autonomia_real,
    estimar_tiempo_tramo,
    calcular_combustible_tramo,
)

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat/2)**2 +
         np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) *
         np.sin(dlon/2)**2)
    return round(2 * R * np.arcsin(np.sqrt(a)), 2)


def optimizar_ruta(
    puerto: dict,
    zonas: list[dict],
    embarcacion: dict,
    combustible_pct: float,
    especie: str,
    altura_olas: float = 0.5,
) -> dict:
    """
    Algoritmo Greedy + 2-opt para encontrar la ruta óptima.

    Estrategia:
    1. Parte del puerto
    2. En cada paso elige la zona con mejor FishScore dentro del combustible restante
    3. Siempre verifica que hay combustible suficiente para retornar al puerto
    4. Aplica mejora 2-opt al final
    """
    if not zonas:
        return _ruta_vacia(puerto, "No hay zonas de pesca disponibles en el rango")

    params = calcular_autonomia_real(embarcacion, combustible_pct)
    vel_kmh      = params["velocidad_kmh"]
    consumo_h    = embarcacion.get("consumo_hora", 20.0)
    cap_bodega   = embarcacion.get("capacidad_bodega", 10.0)

    combustible_disponible = params["combustible_total_l"]
    carga_acumulada = 0.0
    pos_lat = puerto["lat"]
    pos_lon = puerto["lon"]

    ruta_nodos = [_nodo_puerto(puerto, "SALIDA")]
    zonas_restantes = list(zonas)
    distancia_total = 0.0
    tiempo_total = 0.0

    while zonas_restantes:
        # Filtrar zonas a las que podemos ir Y volver
        candidatas = []
        for z in zonas_restantes:
            dist_ida     = haversine_km(pos_lat, pos_lon, z["lat"], z["lon"])
            dist_vuelta  = haversine_km(z["lat"], z["lon"], puerto["lat"], puerto["lon"])
            comb_ida     = calcular_combustible_tramo(dist_ida, vel_kmh, consumo_h)
            comb_vuelta  = calcular_combustible_tramo(dist_vuelta, vel_kmh, consumo_h)
            comb_necesario = comb_ida + comb_vuelta

            if comb_necesario <= combustible_disponible:
                candidatas.append({
                    **z,
                    "_dist_ida":    dist_ida,
                    "_comb_ida":    comb_ida,
                    "_comb_vuelta": comb_vuelta,
                })

        if not candidatas:
            break  # no hay zonas alcanzables con el combustible restante

        # Elegir la zona con mayor FishScore entre las candidatas
        elegida = max(candidatas, key=lambda x: x["fish_score"])

        # Actualizar estado
        combustible_disponible -= elegida["_comb_ida"]
        distancia_total        += elegida["_dist_ida"]
        tiempo_total           += estimar_tiempo_tramo(elegida["_dist_ida"], vel_kmh, altura_olas)
        carga_acumulada         = min(carga_acumulada + _estimar_captura(elegida), cap_bodega)
        pos_lat, pos_lon        = elegida["lat"], elegida["lon"]

        ruta_nodos.append({
            "tipo":              "ZONA_PESCA",
            "lat":               elegida["lat"],
            "lon":               elegida["lon"],
            "fish_score":        elegida["fish_score"],
            "clorofila":         elegida.get("clorofila"),
            "temperatura_c":     elegida.get("temperatura_c"),
            "nivel_chla":        elegida.get("nivel_chla", ""),
            "distancia_desde_anterior_km": elegida["_dist_ida"],
            "carga_acumulada_tm": round(carga_acumulada, 2),
        })

        zonas_restantes.remove(next(z for z in zonas_restantes
                               if z["lat"] == elegida["lat"] and z["lon"] == elegida["lon"]))

        # Parar si bodega llena
        if carga_acumulada >= cap_bodega:
            break

    # Retorno al puerto
    dist_retorno = haversine_km(pos_lat, pos_lon, puerto["lat"], puerto["lon"])
    tiempo_total += estimar_tiempo_tramo(dist_retorno, vel_kmh, altura_olas)
    distancia_total += dist_retorno
    ruta_nodos.append(_nodo_puerto(puerto, "RETORNO"))

    # Mejora 2-opt sobre las zonas de pesca
    zonas_en_ruta = [n for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"]
    if len(zonas_en_ruta) > 2:
        zonas_en_ruta = _two_opt(zonas_en_ruta, puerto)
        ruta_nodos = (
            [ruta_nodos[0]] +
            zonas_en_ruta +
            [ruta_nodos[-1]]
        )

    zonas_visitadas = len([n for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"])
    fish_score_promedio = round(
        np.mean([n["fish_score"] for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"]) if zonas_visitadas else 0, 1
    )

    return {
        "puerto_salida":        puerto["nombre"],
        "especie_objetivo":     especie,
        "distancia_total_km":   round(distancia_total, 1),
        "tiempo_total_horas":   round(tiempo_total, 1),
        "carga_estimada_tm":    round(carga_acumulada, 2),
        "zonas_visitadas":      zonas_visitadas,
        "fish_score_promedio":  fish_score_promedio,
        "combustible_usado_l":  round(params["combustible_total_l"] - combustible_disponible, 1),
        "ruta":                 ruta_nodos,
        "resumen": (
            f"Ruta de {zonas_visitadas} zonas de pesca | "
            f"{round(distancia_total,1)} km | "
            f"{round(tiempo_total,1)} horas | "
            f"~{round(carga_acumulada,1)} TM estimadas"
        )
    }


def _estimar_captura(zona: dict) -> float:
    """Estima TM de pesca en una zona según FishScore. Muy conservador."""
    fs = zona.get("fish_score", 0)
    if fs >= 70:   return 2.5
    elif fs >= 50: return 1.5
    elif fs >= 30: return 0.8
    else:          return 0.3


def _two_opt(zonas: list, puerto: dict) -> list:
    """Mejora el orden de visita de zonas para reducir distancia total."""
    mejor = list(zonas)
    mejorado = True
    while mejorado:
        mejorado = False
        for i in range(len(mejor) - 1):
            for j in range(i + 1, len(mejor)):
                nueva = mejor[:i] + mejor[i:j+1][::-1] + mejor[j+1:]
                if _distancia_ruta(nueva, puerto) < _distancia_ruta(mejor, puerto):
                    mejor = nueva
                    mejorado = True
    return mejor


def _distancia_ruta(zonas: list, puerto: dict) -> float:
    total = haversine_km(puerto["lat"], puerto["lon"], zonas[0]["lat"], zonas[0]["lon"])
    for i in range(len(zonas) - 1):
        total += haversine_km(zonas[i]["lat"], zonas[i]["lon"], zonas[i+1]["lat"], zonas[i+1]["lon"])
    total += haversine_km(zonas[-1]["lat"], zonas[-1]["lon"], puerto["lat"], puerto["lon"])
    return total


def _nodo_puerto(puerto: dict, tipo: str) -> dict:
    return {
        "tipo":  tipo,
        "lat":   puerto["lat"],
        "lon":   puerto["lon"],
        "nombre": puerto["nombre"],
        "fish_score": 0,
        "carga_acumulada_tm": 0,
    }


def _ruta_vacia(puerto: dict, mensaje: str) -> dict:
    return {
        "puerto_salida": puerto["nombre"],
        "distancia_total_km": 0,
        "tiempo_total_horas": 0,
        "carga_estimada_tm": 0,
        "zonas_visitadas": 0,
        "fish_score_promedio": 0,
        "combustible_usado_l": 0,
        "ruta": [],
        "resumen": mensaje,
    }