"""
Pipeline ML Predictivo — FishRoute Pro
Modelo: GradientBoostingRegressor (scikit-learn)

Predice captura_real_tm a partir de features oceanográficas y operacionales.
Se entrena con datos históricos de HistorialRuta donde captura_real_tm no es NULL.

Uso:
    from services.ml_service import predecir_captura, entrenar_modelo, estado_modelo
"""
import os
import math
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Directorio donde se guarda el modelo serializado
MODEL_DIR  = Path(__file__).parent.parent / "ml_models"
MODEL_PATH = MODEL_DIR / "fish_model.pkl"
META_PATH  = MODEL_DIR / "fish_model_meta.json"

# Mínimo de muestras para entrenar
MIN_SAMPLES = 10

# Mapa de especies a número (encoding ordinal simple)
ESPECIE_MAP = {"ANCHOVETA": 0, "BONITO": 1, "CABALLA": 2, "JUREL": 3}

_model = None       # modelo cargado en memoria
_model_meta = {}    # metadatos del último entrenamiento


def _cargar_modelo():
    """Carga el modelo desde disco si existe."""
    global _model, _model_meta
    if not MODEL_PATH.exists():
        return False
    try:
        import joblib, json
        _model = joblib.load(MODEL_PATH)
        if META_PATH.exists():
            with open(META_PATH) as f:
                _model_meta = json.load(f)
        return True
    except Exception as e:
        logger.warning(f"No se pudo cargar modelo ML: {e}")
        return False


def _features_to_vector(features: dict) -> list:
    """
    Convierte un dict de features a un vector numérico para el modelo.
    Orden fijo: fish_score, clorofila, temperatura, olas, viento, distancia, zonas, especie_enc, mes
    """
    especie_enc = ESPECIE_MAP.get(str(features.get("especie", "ANCHOVETA")).upper(), 0)
    mes = features.get("mes", datetime.now().month)
    return [
        float(features.get("fish_score",   50.0)),
        float(features.get("clorofila",     0.5)),
        float(features.get("temperatura",  17.0)),
        float(features.get("olas",          0.5)),
        float(features.get("viento",       15.0)),
        float(features.get("distancia",    100.0)),
        float(features.get("zonas",          2.0)),
        float(especie_enc),
        float(mes),
    ]


def predecir_captura(features: dict) -> float | None:
    """
    Predice captura_real_tm para una zona dada.
    Retorna None si el modelo no está disponible (el llamador usa bins fijos como fallback).
    """
    global _model
    if _model is None:
        _cargar_modelo()
    if _model is None:
        return None
    try:
        X = [_features_to_vector(features)]
        pred = float(_model.predict(X)[0])
        return max(0.1, round(pred, 2))
    except Exception as e:
        logger.warning(f"Error en predicción ML: {e}")
        return None


async def entrenar_modelo(db) -> dict:
    """
    Entrena o re-entrena el modelo con todos los registros donde captura_real_tm IS NOT NULL.
    Retorna métricas del entrenamiento.
    """
    from sqlalchemy import select, text
    from models import HistorialRuta, Embarcacion

    # Obtener datos de entrenamiento
    result = await db.execute(
        select(HistorialRuta).where(HistorialRuta.captura_real_tm.isnot(None))
    )
    registros = result.scalars().all()

    if len(registros) < MIN_SAMPLES:
        return {
            "ok": False,
            "mensaje": f"Datos insuficientes. Se necesitan mínimo {MIN_SAMPLES} capturas reportadas, hay {len(registros)}.",
            "samples": len(registros),
        }

    # Construir dataset
    X, y = [], []
    for r in registros:
        mes = r.fecha_calculo.month if r.fecha_calculo else datetime.now().month
        features = {
            "fish_score":  r.fish_score_promedio  or 50.0,
            "clorofila":   r.clorofila_promedio    or 0.5,
            "temperatura": r.temp_mar_c            or 17.0,
            "olas":        r.condicion_olas_m       or 0.5,
            "viento":      r.condicion_viento       or 15.0,
            "distancia":   r.distancia_total_km    or 100.0,
            "zonas":       r.zonas_visitadas_num    or 2.0,
            "especie":     r.especie_objetivo       or "ANCHOVETA",
            "mes":         mes,
        }
        X.append(_features_to_vector(features))
        y.append(float(r.captura_real_tm))

    # Entrenar GradientBoostingRegressor
    try:
        import joblib, json
        from sklearn.ensemble import GradientBoostingRegressor
        from sklearn.model_selection import cross_val_score
        import numpy as np

        modelo = GradientBoostingRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            random_state=42,
        )
        modelo.fit(X, y)

        # Cross-validation R² (solo si hay suficientes datos)
        r2 = None
        if len(registros) >= 20:
            scores = cross_val_score(modelo, X, y, cv=min(5, len(registros)//4), scoring="r2")
            r2 = round(float(np.mean(scores)), 3)

        # Guardar modelo
        MODEL_DIR.mkdir(exist_ok=True)
        joblib.dump(modelo, MODEL_PATH)

        meta = {
            "fecha_entrenamiento": datetime.now().isoformat(),
            "samples": len(registros),
            "r2_cv":   r2,
            "features": ["fish_score", "clorofila", "temperatura", "olas", "viento", "distancia", "zonas", "especie_enc", "mes"],
        }
        with open(META_PATH, "w") as f:
            json.dump(meta, f, indent=2)

        # Recargar en memoria
        global _model, _model_meta
        _model      = modelo
        _model_meta = meta

        return {
            "ok":      True,
            "samples": len(registros),
            "r2_cv":   r2,
            "mensaje": f"Modelo entrenado con {len(registros)} muestras." + (f" R² = {r2}" if r2 else ""),
        }

    except Exception as e:
        logger.error(f"Error entrenando modelo: {e}")
        return {"ok": False, "mensaje": str(e), "samples": len(registros)}


def estado_modelo() -> dict:
    """Retorna el estado actual del modelo ML."""
    global _model, _model_meta
    if _model is None:
        _cargar_modelo()

    if _model is None:
        return {
            "activo": False,
            "mensaje": "Modelo no entrenado. Reporta capturas reales y llama /ml/entrenar.",
        }

    return {
        "activo":               True,
        "samples":              _model_meta.get("samples"),
        "r2_cv":                _model_meta.get("r2_cv"),
        "fecha_entrenamiento":  _model_meta.get("fecha_entrenamiento"),
        "mensaje":              "Modelo activo y prediciendo capturas.",
    }


# Intentar cargar modelo al importar el módulo
_cargar_modelo()
