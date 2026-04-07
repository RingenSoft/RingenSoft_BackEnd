import redis
import json
import hashlib
from functools import wraps

# Conexión a Redis local
_client = None
_redis_unavailable = False  # evita reconectar en cada llamada cuando Redis no está disponible

def get_redis():
    global _client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _client is None:
        try:
            _client = redis.Redis(
                host="localhost", port=6379, db=0,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
            )
            _client.ping()
        except Exception:
            _client = None
            _redis_unavailable = True  # no volver a intentar hasta restart
    return _client


def cache_get(key: str):
    """Obtiene valor del cache. Retorna None si no existe o Redis no disponible."""
    r = get_redis()
    if r is None:
        return None
    try:
        val = r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def cache_set(key: str, value: dict, ttl_segundos: int = 1800):
    """Guarda valor en cache con tiempo de expiración."""
    r = get_redis()
    if r is None:
        return
    try:
        r.setex(key, ttl_segundos, json.dumps(value))
    except Exception:
        pass


def make_key(prefijo: str, lat: float, lon: float, extra: str = "") -> str:
    """Genera clave de cache basada en coordenadas redondeadas a 0.5 grados."""
    lat_r = round(lat * 2) / 2
    lon_r = round(lon * 2) / 2
    raw = f"{prefijo}:{lat_r}:{lon_r}:{extra}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# TTLs por tipo de dato
TTL_CLIMA   = 60 * 60       # 1 hora  — clima cambia lento
TTL_OCEAN   = 60 * 30       # 30 min  — SST y clorofila
TTL_SCORE   = 60 * 30       # 30 min  — FishScore