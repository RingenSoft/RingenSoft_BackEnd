def calcular_autonomia_real(embarcacion: dict, combustible_pct: float = 1.0) -> dict:
    """
    Calcula parámetros reales de operación según estado de la embarcación.
    """
    velocidad   = embarcacion.get("velocidad_promedio", 10.0)   # nudos
    consumo_h   = embarcacion.get("consumo_hora", 20.0)          # litros/hora
    autonomia_h = embarcacion.get("autonomia_horas", 24.0)       # horas tanque lleno
    anio        = embarcacion.get("anio_fabricacion", 2015)
    tripulacion = embarcacion.get("tripulacion_max", 6)

    # Factor de degradación por antigüedad (1% por año, máx 20%)
    anios_uso = max(0, 2025 - anio)
    factor_edad = max(0.80, 1.0 - anios_uso * 0.01)

    # Factor de carga por tripulación (cada persona ~0.5% más consumo)
    factor_tripulacion = 1.0 + (tripulacion * 0.005)

    # Autonomía real ajustada
    autonomia_real_h = autonomia_h * combustible_pct * factor_edad / factor_tripulacion

    # Radio máximo: reservar 40% para retorno seguro
    velocidad_kmh = velocidad * 1.852
    horas_ida     = autonomia_real_h * 0.60
    radio_km      = velocidad_kmh * horas_ida

    # Consumo total estimado del viaje
    combustible_total_l = autonomia_real_h * consumo_h

    return {
        "velocidad_kmh":        round(velocidad_kmh, 1),
        "velocidad_nudos":      velocidad,
        "autonomia_real_horas": round(autonomia_real_h, 1),
        "radio_max_km":         round(radio_km, 1),
        "combustible_total_l":  round(combustible_total_l, 1),
        "factor_edad":          round(factor_edad, 2),
        "factor_tripulacion":   round(factor_tripulacion, 2),
    }


def estimar_tiempo_tramo(dist_km: float, velocidad_kmh: float, altura_olas: float = 0) -> float:
    """
    Estima horas para recorrer un tramo.
    Penaliza velocidad según altura de olas.
    """
    if altura_olas >= 2.0:
        factor_olas = 0.70   # olas altas reducen velocidad 30%
    elif altura_olas >= 1.0:
        factor_olas = 0.85
    else:
        factor_olas = 1.0

    vel_real = velocidad_kmh * factor_olas
    return round(dist_km / vel_real, 2) if vel_real > 0 else 0


def calcular_combustible_tramo(dist_km: float, velocidad_kmh: float, consumo_hora: float) -> float:
    """Litros consumidos en un tramo."""
    horas = dist_km / velocidad_kmh if velocidad_kmh > 0 else 0
    return round(horas * consumo_hora, 1)