import numpy as np
from shapely.geometry import Point, Polygon

# Zona Económica Exclusiva peruana (Pacífico Sur Oriental)
# Polígono simplificado de la costa peruana — solo mar adentro
POLIGONO_MAR_PERUANO = Polygon([
    (-85.0, -3.0),
    (-82.0, -3.5),
    (-81.5, -5.0),
    (-81.2, -6.5),
    (-80.8, -8.0),
    (-80.5, -9.5),
    (-80.0, -11.0),
    (-79.5, -12.5),
    (-79.0, -14.0),
    (-78.5, -15.5),
    (-77.5, -17.0),
    (-72.5, -18.5),
    (-68.0, -18.5),
    (-68.0, -3.0),
    (-85.0, -3.0),
])


# Distancia mínima de la costa en grados (~55km mar adentro)
OFFSET_COSTA = 0.5

# Resolución de la grilla — 0.5 grados ~ 55km entre puntos
RESOLUCION_GRADOS = 0.5

# Puertos pesqueros del Perú con coordenadas GPS reales (norte a sur)
PUERTOS_PERU = [
    {"id": "TUMBES",        "nombre": "Tumbes",           "lat": -3.57,  "lon": -80.45},
    {"id": "TALARA",        "nombre": "Talara",           "lat": -4.57,  "lon": -81.27},
    {"id": "PAITA",         "nombre": "Paita",            "lat": -5.09,  "lon": -81.11},
    {"id": "SECHURA",       "nombre": "Sechura",          "lat": -5.56,  "lon": -80.82},
    {"id": "CHICAMA",       "nombre": "Puerto Chicama",   "lat": -7.70,  "lon": -79.45},
    {"id": "SALAVERRY",     "nombre": "Salaverry",        "lat": -8.22,  "lon": -78.98},
    {"id": "CHIMBOTE",      "nombre": "Chimbote",         "lat": -9.07,  "lon": -78.59},
    {"id": "COISHCO",       "nombre": "Coishco",          "lat": -9.01,  "lon": -78.58},
    {"id": "CASMA",         "nombre": "Casma",            "lat": -9.47,  "lon": -78.32},
    {"id": "HUARMEY",       "nombre": "Huarmey",          "lat": -10.07, "lon": -78.15},
    {"id": "SUPE",          "nombre": "Supe",             "lat": -10.79, "lon": -77.78},
    {"id": "HUACHO",        "nombre": "Huacho",           "lat": -11.11, "lon": -77.61},
    {"id": "CHANCAY",       "nombre": "Chancay",          "lat": -11.57, "lon": -77.27},
    {"id": "CALLAO",        "nombre": "Callao",           "lat": -12.05, "lon": -77.15},
    {"id": "PUCUSANA",      "nombre": "Pucusana",         "lat": -12.47, "lon": -76.80},
    {"id": "PISCO",         "nombre": "Pisco",            "lat": -13.71, "lon": -76.22},
    {"id": "TAMBO_DE_MORA", "nombre": "Tambo de Mora",   "lat": -13.47, "lon": -76.19},
    {"id": "ATICO",         "nombre": "Atico",            "lat": -16.23, "lon": -73.68},
    {"id": "QUILCA",        "nombre": "Quilca",           "lat": -16.72, "lon": -72.42},
    {"id": "CAMANÃ",        "nombre": "Camaná",           "lat": -16.62, "lon": -72.71},
    {"id": "MOLLENDO",      "nombre": "Mollendo",         "lat": -17.02, "lon": -72.01},
    {"id": "MATARANI",      "nombre": "Matarani",         "lat": -17.00, "lon": -72.11},
    {"id": "ILO",           "nombre": "Ilo",              "lat": -17.64, "lon": -71.34},
]

# Cache en memoria para no recalcular la grilla en cada request
_grilla_cache = None

def generar_grilla() -> list[dict]:
    """
    Genera puntos en el mar peruano con resolución de ~55km.
    Solo incluye puntos dentro del polígono del mar peruano.
    Cachea el resultado en memoria.
    """
    global _grilla_cache
    if _grilla_cache is not None:
        return _grilla_cache

    lats = np.arange(-18.0, -3.5, RESOLUCION_GRADOS)
    lons = np.arange(-82.0, -70.0, RESOLUCION_GRADOS)

    puntos = []
    for lat in lats:
        for lon in lons:
            punto = Point(lon, lat)
            if POLIGONO_MAR_PERUANO.contains(punto) and es_suficientemente_offshore(float(lat), float(lon)):                
                puntos.append({
                    "lat": round(float(lat), 4),
                    "lon": round(float(lon), 4),
                    "fish_score": 0.0,
                    "temperatura_c": None,
                    "clorofila": None,
                    "score_temp": 0.0,
                    "score_chla": 0.0,
                    "score_clima": 0.0,
                })

    _grilla_cache = puntos
    return puntos


def filtrar_por_radio(
    puntos: list[dict],
    lat_origen: float,
    lon_origen: float,
    radio_km: float
) -> list[dict]:
    """
    Filtra puntos dentro de un radio en km desde el puerto de origen.
    Usa fórmula de Haversine para distancia precisa.
    """
    R = 6371.0
    resultado = []
    for p in puntos:
        dlat = np.radians(p["lat"] - lat_origen)
        dlon = np.radians(p["lon"] - lon_origen)
        a = (np.sin(dlat / 2) ** 2 +
             np.cos(np.radians(lat_origen)) *
             np.cos(np.radians(p["lat"])) *
             np.sin(dlon / 2) ** 2)
        distancia_km = 2 * R * np.arcsin(np.sqrt(a))
        if distancia_km <= radio_km:
            resultado.append({**p, "distancia_km": round(distancia_km, 1)})
    return resultado


def calcular_radio_km(
    velocidad_nudos: float,
    autonomia_horas: float,
    combustible_actual_pct: float = 1.0
) -> float:
    """
    Calcula el radio máximo de operación en km.
    Reserva siempre el 40% del combustible para el retorno seguro.
    """
    velocidad_kmh = velocidad_nudos * 1.852
    horas_disponibles = autonomia_horas * combustible_actual_pct
    horas_ida = horas_disponibles * 0.60  # 60% para ir, 40% para volver
    radio = velocidad_kmh * horas_ida
    return round(radio, 1)


def get_puerto(id_puerto: str) -> dict | None:
    """Retorna datos del puerto por ID."""
    for p in PUERTOS_PERU:
        if p["id"] == id_puerto.upper():
            return p
    return None

COSTA_APROXIMADA = [
    (-3.5,  -80.5), (-4.0, -81.0), (-5.0, -81.0),
    (-6.0,  -80.7), (-7.0, -79.8), (-8.0, -79.4),
    (-9.0,  -78.6), (-10.0,-78.0), (-11.0,-77.6),
    (-12.0, -77.2), (-13.0,-76.5), (-14.0,-76.0),
    (-15.0, -75.3), (-16.0,-74.5), (-17.0,-72.0),
    (-18.0, -70.8),
]

def es_suficientemente_offshore(lat: float, lon: float) -> bool:
    MIN_DIST_GRADOS = 0.8
    for c_lat, c_lon in COSTA_APROXIMADA:
        if abs(lat - c_lat) < 1.5:
            if lon > c_lon - MIN_DIST_GRADOS:
                return False
    return True