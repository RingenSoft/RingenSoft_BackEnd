"""
Optimizador de rutas de pesca — Greedy + Or-opt VRP.
Soporta tres modos de optimización:
  - "equilibrado"    : balancea FishScore y distancia (default)
  - "max_captura"    : prioriza zonas de mayor FishScore sin importar distancia
  - "min_combustible": prioriza zonas más cercanas con FishScore mínimo aceptable
"""
import numpy as np
from services.vessel_model import (
    calcular_autonomia_real,
    estimar_tiempo_tramo,
    calcular_combustible_tramo,
)

# Intentar usar corrientes si están disponibles (no bloquea si falla)
try:
    from services.currents_service import ajustar_velocidad_por_corriente
    _CORRIENTES_OK = True
except ImportError:
    _CORRIENTES_OK = False


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
    modo: str = "equilibrado",
    corrientes: dict | None = None,   # {(lat,lon): {"u":..., "v":...}}
) -> dict:
    """
    Algoritmo Greedy + Or-opt para encontrar la ruta óptima.

    Parámetros:
      modo        : "equilibrado" | "max_captura" | "min_combustible"
      corrientes  : dict con corrientes por zona {(lat,lon): {u, v}}

    Estrategia:
    1. Parte del puerto
    2. En cada paso elige la zona más conveniente según el modo
    3. Siempre verifica que hay combustible suficiente para retornar
    4. Aplica Or-opt al final para mejorar el orden de visita
    """
    if not zonas:
        return _ruta_vacia(puerto, "No hay zonas de pesca disponibles en el rango")

    corrientes = corrientes or {}
    params      = calcular_autonomia_real(embarcacion, combustible_pct)
    vel_kmh     = params["velocidad_kmh"]
    consumo_h   = embarcacion.get("consumo_hora", 20.0)
    cap_bodega  = embarcacion.get("capacidad_bodega", 10.0)

    combustible_disponible = params["combustible_total_l"]
    carga_acumulada = 0.0
    pos_lat = puerto["lat"]
    pos_lon = puerto["lon"]

    ruta_nodos     = [_nodo_puerto(puerto, "SALIDA")]
    zonas_restantes = list(zonas)
    distancia_total = 0.0
    tiempo_total    = 0.0

    while zonas_restantes:
        candidatas = []
        for z in zonas_restantes:
            dist_ida    = haversine_km(pos_lat, pos_lon, z["lat"], z["lon"])
            dist_vuelta = haversine_km(z["lat"], z["lon"], puerto["lat"], puerto["lon"])

            # Ajustar velocidad por corrientes si disponibles
            cor = corrientes.get((z["lat"], z["lon"]), {"u": 0.0, "v": 0.0})
            vel_ida    = ajustar_velocidad_por_corriente(vel_kmh, pos_lat, pos_lon, z["lat"], z["lon"], cor["u"], cor["v"]) if _CORRIENTES_OK else vel_kmh
            vel_vuelta = ajustar_velocidad_por_corriente(vel_kmh, z["lat"], z["lon"], puerto["lat"], puerto["lon"], cor["u"], cor["v"]) if _CORRIENTES_OK else vel_kmh

            comb_ida    = calcular_combustible_tramo(dist_ida,    vel_ida,    consumo_h)
            comb_vuelta = calcular_combustible_tramo(dist_vuelta, vel_vuelta, consumo_h)

            if (comb_ida + comb_vuelta) <= combustible_disponible:
                candidatas.append({
                    **z,
                    "_dist_ida":    dist_ida,
                    "_comb_ida":    comb_ida,
                    "_comb_vuelta": comb_vuelta,
                    "_vel_ida":     vel_ida,
                })

        if not candidatas:
            break

        elegida = _seleccionar_zona(candidatas, modo)

        combustible_disponible -= elegida["_comb_ida"]
        distancia_total        += elegida["_dist_ida"]
        tiempo_total           += estimar_tiempo_tramo(elegida["_dist_ida"], elegida["_vel_ida"], altura_olas)
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

        zonas_restantes.remove(next(
            z for z in zonas_restantes
            if z["lat"] == elegida["lat"] and z["lon"] == elegida["lon"]
        ))

        if carga_acumulada >= cap_bodega:
            break

    # Retorno al puerto
    dist_retorno = haversine_km(pos_lat, pos_lon, puerto["lat"], puerto["lon"])
    tiempo_total += estimar_tiempo_tramo(dist_retorno, vel_kmh, altura_olas)
    distancia_total += dist_retorno
    ruta_nodos.append(_nodo_puerto(puerto, "RETORNO"))

    # Or-opt: mejora el orden de zonas de pesca
    zonas_en_ruta = [n for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"]
    if len(zonas_en_ruta) >= 2:
        zonas_en_ruta = _or_opt(zonas_en_ruta, puerto)
        ruta_nodos = [ruta_nodos[0]] + zonas_en_ruta + [ruta_nodos[-1]]
        # Recalcular distancia y tiempo con el orden optimizado
        distancia_total, tiempo_total = _recalcular_metricas(ruta_nodos, vel_kmh, consumo_h, altura_olas)

    zonas_visitadas = len([n for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"])
    fish_scores = [n["fish_score"] for n in ruta_nodos if n["tipo"] == "ZONA_PESCA"]
    fish_score_promedio = round(float(np.mean(fish_scores)), 1) if fish_scores else 0.0

    combustible_usado = round(params["combustible_total_l"] - combustible_disponible, 1)

    modo_labels = {
        "equilibrado":    "Equilibrada",
        "max_captura":    "Máxima captura",
        "min_combustible": "Mínimo combustible",
    }

    return {
        "modo":                 modo,
        "modo_label":           modo_labels.get(modo, modo),
        "puerto_salida":        puerto["nombre"],
        "especie_objetivo":     especie,
        "distancia_total_km":   round(distancia_total, 1),
        "tiempo_total_horas":   round(tiempo_total, 1),
        "carga_estimada_tm":    round(carga_acumulada, 2),
        "zonas_visitadas":      zonas_visitadas,
        "fish_score_promedio":  fish_score_promedio,
        "combustible_usado_l":  combustible_usado,
        "ruta":                 ruta_nodos,
        "resumen": (
            f"[{modo_labels.get(modo, modo)}] "
            f"{zonas_visitadas} zonas | "
            f"{round(distancia_total,1)} km | "
            f"{round(tiempo_total,1)} h | "
            f"~{round(carga_acumulada,1)} TM estimadas"
        )
    }


# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------

def _seleccionar_zona(candidatas: list, modo: str) -> dict:
    """Elige la mejor zona candidata según el modo de optimización."""
    if modo == "max_captura":
        # Solo maximiza FishScore, sin considerar distancia
        return max(candidatas, key=lambda x: x["fish_score"])

    elif modo == "min_combustible":
        # Maximiza FishScore / distancia (eficiencia de pesca por km)
        # Solo considera zonas con fish_score ≥ 30 para evitar zonas malas cercanas
        validas = [c for c in candidatas if c["fish_score"] >= 30]
        if not validas:
            validas = candidatas
        return min(validas, key=lambda x: x["_dist_ida"] / max(x["fish_score"], 1))

    else:  # equilibrado (default)
        return max(candidatas, key=lambda x: x["fish_score"])


def _estimar_captura(zona: dict) -> float:
    """
    Estima TM de pesca en una zona según FishScore.
    Si el servicio ML está disponible, lo usa. Si no, usa bins fijos como fallback.
    """
    try:
        from services.ml_service import predecir_captura
        features = {
            "fish_score":   zona.get("fish_score", 0),
            "clorofila":    zona.get("clorofila", 0.5),
            "temperatura":  zona.get("temperatura_c", 17.0),
            "especie":      zona.get("especie", "ANCHOVETA"),
        }
        pred = predecir_captura(features)
        if pred is not None:
            return pred
    except Exception:
        pass

    # Fallback: bins fijos
    fs = zona.get("fish_score", 0)
    if fs >= 70:   return 2.5
    elif fs >= 50: return 1.5
    elif fs >= 30: return 0.8
    else:          return 0.3


def _or_opt(zonas: list, puerto: dict) -> list:
    """
    Or-opt: mueve segmentos de 1 o 2 zonas a la mejor posición disponible.
    Más efectivo que 2-opt para encontrar rutas cortas.
    """
    mejor = list(zonas)
    mejorado = True
    while mejorado:
        mejorado = False
        for seg_len in [1, 2]:
            for i in range(len(mejor)):
                if i + seg_len > len(mejor):
                    continue
                segmento = mejor[i:i + seg_len]
                resto    = mejor[:i] + mejor[i + seg_len:]
                dist_actual = _distancia_ruta(mejor, puerto)
                for j in range(len(resto) + 1):
                    nueva = resto[:j] + segmento + resto[j:]
                    if _distancia_ruta(nueva, puerto) < dist_actual - 0.01:
                        mejor    = nueva
                        mejorado = True
                        break
                if mejorado:
                    break
            if mejorado:
                break
    return mejor


def _distancia_ruta(zonas: list, puerto: dict) -> float:
    if not zonas:
        return 0.0
    total = haversine_km(puerto["lat"], puerto["lon"], zonas[0]["lat"], zonas[0]["lon"])
    for i in range(len(zonas) - 1):
        total += haversine_km(zonas[i]["lat"], zonas[i]["lon"], zonas[i+1]["lat"], zonas[i+1]["lon"])
    total += haversine_km(zonas[-1]["lat"], zonas[-1]["lon"], puerto["lat"], puerto["lon"])
    return total


def _recalcular_metricas(nodos: list, vel_kmh: float, consumo_h: float, altura_olas: float):
    """Recalcula distancia y tiempo totales tras el Or-opt."""
    distancia = 0.0
    tiempo    = 0.0
    for i in range(len(nodos) - 1):
        d = haversine_km(nodos[i]["lat"], nodos[i]["lon"], nodos[i+1]["lat"], nodos[i+1]["lon"])
        distancia += d
        tiempo    += estimar_tiempo_tramo(d, vel_kmh, altura_olas)
    return distancia, tiempo


def _nodo_puerto(puerto: dict, tipo: str) -> dict:
    return {
        "tipo":   tipo,
        "lat":    puerto["lat"],
        "lon":    puerto["lon"],
        "nombre": puerto["nombre"],
        "fish_score": 0,
        "carga_acumulada_tm": 0,
    }


def _ruta_vacia(puerto: dict, mensaje: str) -> dict:
    return {
        "modo":                "equilibrado",
        "modo_label":          "Equilibrada",
        "puerto_salida":       puerto["nombre"],
        "distancia_total_km":  0,
        "tiempo_total_horas":  0,
        "carga_estimada_tm":   0,
        "zonas_visitadas":     0,
        "fish_score_promedio": 0,
        "combustible_usado_l": 0,
        "ruta":                [],
        "resumen":             mensaje,
    }
