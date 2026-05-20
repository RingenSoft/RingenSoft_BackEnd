"""
Calendario de vedas pesqueras del Perú.
Basado en datos históricos de PRODUCE e IMARPE.
Las fechas son orientativas — se publican anualmente mediante Resolución Ministerial.
"""
from datetime import date

# Periodos típicos de veda por especie para la zona norte-centro del Perú.
# Formato: (mes_inicio, dia_inicio, mes_fin, dia_fin, descripción)
VEDAS: dict[str, list[dict]] = {
    "ANCHOVETA": [
        {
            "mes_inicio": 2, "dia_inicio": 15,
            "mes_fin":    3, "dia_fin":    31,
            "descripcion": "Veda reproductiva 1.ª temporada norte-centro (feb-mar)",
        },
        {
            "mes_inicio": 7, "dia_inicio": 15,
            "mes_fin":    9, "dia_fin":    15,
            "descripcion": "Veda reproductiva 2.ª temporada norte-centro (jul-sep)",
        },
    ],
    "CABALLA": [
        {
            "mes_inicio": 1, "dia_inicio": 1,
            "mes_fin":    2, "dia_fin":    28,
            "descripcion": "Veda reproductiva caballa — enero/febrero",
        },
    ],
    "JUREL": [
        {
            "mes_inicio": 1, "dia_inicio": 1,
            "mes_fin":    3, "dia_fin":    31,
            "descripcion": "Veda reproductiva jurel — primer trimestre",
        },
    ],
    "BONITO": [],   # Sin veda fija establecida por PRODUCE
}


def verificar_veda(especie: str, fecha_str: str) -> dict:
    """
    Verifica si una especie está en período de veda en una fecha dada.
    fecha_str debe estar en formato 'YYYY-MM-DD'.
    """
    especie   = especie.upper()
    periodos  = VEDAS.get(especie, [])

    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        return {"en_veda": False, "especie": especie, "advertencia": ""}

    for p in periodos:
        inicio = date(fecha.year, p["mes_inicio"], p["dia_inicio"])
        fin    = date(fecha.year, p["mes_fin"],    p["dia_fin"])
        if inicio <= fecha <= fin:
            return {
                "en_veda":     True,
                "especie":     especie,
                "descripcion": p["descripcion"],
                "periodo":     f"{inicio.strftime('%d/%m')} — {fin.strftime('%d/%m/%Y')}",
                "advertencia": (
                    f"⚠️ {especie} puede estar en veda del {inicio.strftime('%d/%m')} "
                    f"al {fin.strftime('%d/%m')}. Verifica con PRODUCE antes de salir."
                ),
            }

    return {"en_veda": False, "especie": especie, "descripcion": "", "advertencia": ""}


def get_vedas_especie(especie: str) -> dict:
    """Retorna todos los períodos de veda del año en curso para la especie dada."""
    especie  = especie.upper()
    periodos = VEDAS.get(especie, [])
    año      = date.today().year

    resultado = []
    for p in periodos:
        inicio = date(año, p["mes_inicio"], p["dia_inicio"])
        fin    = date(año, p["mes_fin"],    p["dia_fin"])
        resultado.append({
            "inicio":      inicio.isoformat(),
            "fin":         fin.isoformat(),
            "descripcion": p["descripcion"],
        })

    return {
        "especie":  especie,
        "año":      año,
        "periodos": resultado,
        "fuente":   "IMARPE / PRODUCE (datos históricos orientativos)",
        "nota":     "Las fechas exactas se publican anualmente por Resolución Ministerial de PRODUCE. Consulte siempre antes de salir.",
    }
