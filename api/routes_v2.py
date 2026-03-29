

import asyncio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend import auth
from backend.models import Usuario, Embarcacion, HistorialRuta
from backend.services.weather_service import obtener_condiciones_mar
from backend.services.ocean_service import obtener_datos_zona
from backend.services.fish_grid import (
    generar_grilla, filtrar_por_radio,
    calcular_radio_km, get_puerto, PUERTOS_PERU
)
from backend.services.fish_score import calcular_scores_zona, seleccionar_mejores_zonas
from backend.services.route_optimizer import optimizar_ruta

router = APIRouter(prefix="/api/v2", tags=["v2"])


# --- SCHEMAS ---
class RutaRequest(BaseModel):
    id_puerto:        str
    especie:          str = "ANCHOVETA"
    combustible_pct:  float = 1.0    # 0.0 a 1.0 (100% = tanque lleno)
    velocidad_nudos:  Optional[float] = None
    autonomia_horas:  Optional[float] = None
    consumo_hora:     Optional[float] = None
    capacidad_bodega: Optional[float] = None
    anio_fabricacion: Optional[int]   = None
    tripulacion:      Optional[int]   = None
    top_zonas:        int = 5
    id_embarcacion:   Optional[str]   = None   # saved to history when provided


# --- ENDPOINTS ---

@router.get("/condiciones")
async def get_condiciones(
    lat:     float = Query(..., example=-9.07),
    lon:     float = Query(..., example=-78.59),
    especie: str   = Query("ANCHOVETA")
):
    """Condiciones actuales de una zona: clima, temperatura, clorofila y FishScore."""
    try:
        clima, ocean = await asyncio.gather(
            obtener_condiciones_mar(lat, lon),
            obtener_datos_zona(lat, lon, especie),
        )
        return {
            "zona": {"latitud": lat, "longitud": lon, "especie": especie},
            "clima": clima,
            "oceanografia": ocean,
            "fish_score_preliminar": round(
                ocean["scores"]["clorofila"]    * 0.35 +
                ocean["scores"]["temperatura"]  * 0.25 +
                (100 - min(clima["mar"]["altura_olas_m"] * 20, 100)) * 0.10,
                1
            )
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/puertos")
async def get_puertos():
    """Lista de puertos pesqueros disponibles."""
    return {"puertos": PUERTOS_PERU}


@router.post("/ruta-optima")
async def post_ruta_optima(req: RutaRequest, db: AsyncSession = Depends(get_db)):
    """
    Calcula la ruta óptima de pesca para una embarcación.
    Usa datos reales de satélite (NASA/NOAA) y algoritmo VRP + 2-opt.
    """
    # 1. Validar puerto
    puerto = get_puerto(req.id_puerto)
    if not puerto:
        ids_validos = [p["id"] for p in PUERTOS_PERU]
        raise HTTPException(
            status_code=404,
            detail=f"Puerto '{req.id_puerto}' no encontrado. Válidos: {ids_validos}"
        )

    # 2. Validar especie
    especies_validas = ["ANCHOVETA", "BONITO", "CABALLA", "JUREL"]
    especie = req.especie.upper()
    if especie not in especies_validas:
        raise HTTPException(
            status_code=400,
            detail=f"Especie inválida. Válidas: {especies_validas}"
        )

    # 3. Construir perfil de embarcación
    embarcacion = {
        "velocidad_promedio": req.velocidad_nudos   or 10.0,
        "consumo_hora":       req.consumo_hora       or 20.0,
        "autonomia_horas":    req.autonomia_horas    or 24.0,
        "capacidad_bodega":   req.capacidad_bodega   or 15.0,
        "anio_fabricacion":   req.anio_fabricacion   or 2015,
        "tripulacion_max":    req.tripulacion        or 6,
    }

    combustible_pct = max(0.1, min(1.0, req.combustible_pct))

    try:
        # 4. Obtener clima en el puerto de salida
        clima = await obtener_condiciones_mar(puerto["lat"], puerto["lon"])

        # 5. Bloquear si condiciones peligrosas
        if not clima["navegacion_segura"]:
            return {
                "status": "BLOQUEADO",
                "alerta": clima["alerta"],
                "mensaje": "No es seguro salir al mar con las condiciones actuales.",
                "ruta": None
            }

        # 6. Generar grilla y filtrar por radio
        radio_km = calcular_radio_km(
            embarcacion["velocidad_promedio"],
            embarcacion["autonomia_horas"],
            combustible_pct
        )
        grilla = generar_grilla()
        puntos_alcanzables = filtrar_por_radio(
            grilla, puerto["lat"], puerto["lon"], radio_km
        )

        if not puntos_alcanzables:
            raise HTTPException(
                status_code=400,
                detail="No hay puntos de pesca en el radio de operación."
            )

        # 7. Calcular FishScores (máximo 20 puntos para velocidad)
        muestra = puntos_alcanzables[:20]
        scored = await calcular_scores_zona(
            muestra, especie, puerto["lat"], puerto["lon"]
        )

        # 8. Seleccionar mejores zonas
        mejores = seleccionar_mejores_zonas(
            scored, top_n=req.top_zonas, score_minimo=20.0
        )

        if not mejores:
            mejores = scored[:3]

        # 9. Optimizar ruta
        altura_olas = clima["mar"]["altura_olas_m"]
        resultado = optimizar_ruta(
            puerto=puerto,
            zonas=mejores,
            embarcacion=embarcacion,
            combustible_pct=combustible_pct,
            especie=especie,
            altura_olas=altura_olas,
        )

        # 10. Guardar en historial si hubo zonas visitadas
        if resultado.get("zonas_visitadas", 0) > 0:
            nueva_ruta = HistorialRuta(
                id_embarcacion     = req.id_embarcacion,
                distancia_total_km = resultado["distancia_total_km"],
                combustible_usado  = resultado["combustible_usado_l"],
                carga_estimada_tm  = resultado["carga_estimada_tm"],
                condicion_olas_m   = altura_olas,
                condicion_viento   = clima["viento"]["velocidad_kmh"],
                temp_mar_c         = scored[0].get("temperatura_c") if scored else None,
                especie_objetivo   = especie,
            )
            db.add(nueva_ruta)
            await db.commit()

        return {
            "status": "OK",
            "alerta": clima["alerta"],
            "radio_operacion_km": radio_km,
            "puntos_evaluados": len(muestra),
            "resultado": resultado,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# --- AUTH ---
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
oauth2_scheme = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):
    token = credentials.credentials
    payload = auth.decodificar_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    username = payload.get("sub")
    result = await db.execute(select(Usuario).where(Usuario.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user


class LoginRequest(BaseModel):
    username: str
    password: str

class RegistroRequest(BaseModel):
    username:        str
    password:        str
    nombre_completo: str


@router.post("/auth/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Usuario).where(Usuario.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not auth.verificar_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = auth.crear_access_token({"sub": user.username})
    return {
        "access_token":  token,
        "token_type":    "bearer",
        "nombre":        user.nombre_completo,
        "rol":           user.rol,
        "id_usuario":    user.id_usuario,
    }


@router.post("/auth/registro")
async def registro(req: RegistroRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Usuario).where(Usuario.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El usuario ya existe")

    nuevo = Usuario(
        username        = req.username,
        password_hash   = auth.encriptar_password(req.password),
        nombre_completo = req.nombre_completo,
        rol             = "PESCADOR",
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)

    token = auth.crear_access_token({"sub": nuevo.username})
    return {
        "access_token": token,
        "token_type":   "bearer",
        "nombre":       nuevo.nombre_completo,
        "id_usuario":   nuevo.id_usuario,
    }


# --- HISTORIAL ---
@router.get("/historial")
async def get_historial(
    id_embarcacion: Optional[str] = Query(None),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retorna el historial de rutas. Acepta ?id_embarcacion=X para filtrar."""
    query = select(HistorialRuta).order_by(HistorialRuta.fecha_calculo.desc())
    if id_embarcacion:
        query = query.where(HistorialRuta.id_embarcacion == id_embarcacion)
    result = await db.execute(query.limit(50))
    rutas = result.scalars().all()
    return {
        "usuario": current_user.nombre_completo,
        "total":   len(rutas),
        "rutas": [
            {
                "id":             r.id_ruta,
                "fecha":          r.fecha_calculo.isoformat() if r.fecha_calculo else None,
                "distancia_km":   r.distancia_total_km,
                "carga_estimada": r.carga_estimada_tm,
                "captura_real":   r.captura_real_tm,
                "especie":        r.especie_objetivo,
                "condicion_olas": r.condicion_olas_m,
                "temp_mar":       r.temp_mar_c,
            }
            for r in rutas
        ]
    }

class CapturaReport(BaseModel):
    id_embarcacion:  str
    captura_real_tm: float
    especie:         str = "ANCHOVETA"
    notas:           Optional[str] = None
    id_ruta:         Optional[int] = None   # si se provee, actualiza esa ruta


@router.post("/captura/reportar")
async def reportar_captura(
    req: CapturaReport,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Reporta la captura real de una salida.
    - Si se envía id_ruta: actualiza la captura de esa ruta existente.
    - Si no se envía id_ruta: crea un registro independiente de captura.
    """
    if req.id_ruta is not None:
        result = await db.execute(
            select(HistorialRuta).where(HistorialRuta.id_ruta == req.id_ruta)
        )
        ruta = result.scalar_one_or_none()
        if not ruta:
            raise HTTPException(status_code=404, detail="Ruta no encontrada")
        ruta.captura_real_tm = req.captura_real_tm
        await db.commit()
        return {
            "mensaje":    "Captura actualizada correctamente",
            "id_ruta":    req.id_ruta,
            "captura_tm": req.captura_real_tm,
            "especie":    req.especie,
        }

    # Sin id_ruta: registro independiente de captura (sin ruta pre-calculada)
    nueva_ruta = HistorialRuta(
        id_embarcacion     = req.id_embarcacion,
        distancia_total_km = None,
        combustible_usado  = None,
        carga_estimada_tm  = None,
        captura_real_tm    = req.captura_real_tm,
        especie_objetivo   = req.especie,
    )
    db.add(nueva_ruta)
    await db.commit()

    return {
        "mensaje":    "Captura registrada correctamente",
        "captura_tm": req.captura_real_tm,
        "especie":    req.especie,
    }


# --- EMBARCACIONES ---
class EmbarcacionCreate(BaseModel):
    nombre:            str
    capacidad_bodega:  float
    velocidad_promedio: float = 10.0
    consumo_hora:      float = 20.0
    autonomia_horas:   float = 24.0
    tipo_motor:        str = "DIESEL"
    material_casco:    str = "FIBRA"
    tripulacion_max:   int = 6
    anio_fabricacion:  int = 2015

@router.get("/embarcaciones")
async def get_embarcaciones(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Embarcacion).where(Embarcacion.owner_id == current_user.id_usuario)
    )
    barcos = result.scalars().all()
    return [
        {
            "id_embarcacion":    b.id_embarcacion,
            "nombre":            b.nombre,
            "capacidad_bodega":  b.capacidad_bodega,
            "velocidad_promedio": b.velocidad_promedio,
            "consumo_hora":      b.consumo_hora,
            "autonomia_horas":   b.autonomia_horas,
            "material_casco":    b.material_casco,
            "tripulacion_max":   b.tripulacion_max,
            "anio_fabricacion":  b.anio_fabricacion,
            "estado":            b.estado,
        }
        for b in barcos
    ]

@router.post("/embarcaciones")
async def crear_embarcacion(
    req: EmbarcacionCreate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    import uuid
    id_nuevo = f"E-{current_user.id_usuario}-{str(uuid.uuid4())[:6].upper()}"
    barco = Embarcacion(
        id_embarcacion    = id_nuevo,
        nombre            = req.nombre,
        capacidad_bodega  = req.capacidad_bodega,
        velocidad_promedio= req.velocidad_promedio,
        consumo_hora      = req.consumo_hora,
        autonomia_horas   = req.autonomia_horas,
        tipo_motor        = req.tipo_motor,
        material_casco    = req.material_casco,
        tripulacion_max   = req.tripulacion_max,
        anio_fabricacion  = req.anio_fabricacion,
        owner_id          = current_user.id_usuario,
    )
    db.add(barco)
    await db.commit()
    await db.refresh(barco)
    return {"id_embarcacion": barco.id_embarcacion, "nombre": barco.nombre}

class EmbarcacionUpdate(BaseModel):
    nombre:             Optional[str]   = None
    capacidad_bodega:   Optional[float] = None
    velocidad_promedio: Optional[float] = None
    consumo_hora:       Optional[float] = None
    autonomia_horas:    Optional[float] = None
    tipo_motor:         Optional[str]   = None
    material_casco:     Optional[str]   = None
    tripulacion_max:    Optional[int]   = None
    anio_fabricacion:   Optional[int]   = None

@router.patch("/embarcaciones/{id_embarcacion}")
async def actualizar_embarcacion(
    id_embarcacion: str,
    req: EmbarcacionUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Updates editable fields of a vessel. Only the owner can update."""
    result = await db.execute(
        select(Embarcacion).where(
            Embarcacion.id_embarcacion == id_embarcacion,
            Embarcacion.owner_id == current_user.id_usuario
        )
    )
    barco = result.scalar_one_or_none()
    if not barco:
        raise HTTPException(status_code=404, detail="Embarcación no encontrada")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(barco, field, value)

    await db.commit()
    await db.refresh(barco)
    return {"mensaje": "Embarcación actualizada", "id_embarcacion": barco.id_embarcacion}


@router.delete("/embarcaciones/{id_embarcacion}")
async def eliminar_embarcacion(
    id_embarcacion: str,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Deletes a vessel. Only the owner can delete it."""
    result = await db.execute(
        select(Embarcacion).where(
            Embarcacion.id_embarcacion == id_embarcacion,
            Embarcacion.owner_id == current_user.id_usuario
        )
    )
    barco = result.scalar_one_or_none()
    if not barco:
        raise HTTPException(status_code=404, detail="Embarcación no encontrada")

    await db.delete(barco)
    await db.commit()
    return {"mensaje": "Embarcación eliminada", "id_embarcacion": id_embarcacion}


@router.get("/zonas-calor")
async def get_zonas_calor(
    puerto_id: str = "CHIMBOTE",
    especie: str = "ANCHOVETA"
):
    """Retorna FishScores de todas las zonas alcanzables para el mapa de calor."""
    from backend.services.fish_grid import generar_grilla, filtrar_por_radio, get_puerto
    from backend.services.fish_score import calcular_scores_zona

    puerto = get_puerto(puerto_id)
    if not puerto:
        raise HTTPException(status_code=404, detail="Puerto no encontrado")

    grilla = generar_grilla()
    puntos = filtrar_por_radio(grilla, puerto["lat"], puerto["lon"], 300)
    muestra = puntos[:15]

    scored = await calcular_scores_zona(muestra, especie, puerto["lat"], puerto["lon"])
    return {
        "puerto": puerto,
        "especie": especie,
        "zonas": [
            {
                "lat":        z["lat"],
                "lon":        z["lon"],
                "fish_score": z["fish_score"],
                "clorofila":  z.get("clorofila"),
                "temperatura": z.get("temperatura_c"),
                "nivel_chla": z.get("nivel_chla", ""),
            }
            for z in scored
        ]
    }
@router.get("/estadisticas")
async def get_estadisticas(db: AsyncSession = Depends(get_db)):
    """Estadísticas agregadas del historial de rutas."""
    result = await db.execute(
        select(HistorialRuta).order_by(HistorialRuta.fecha_calculo.desc())
    )
    rutas = result.scalars().all()

    if not rutas:
        return {"total": 0, "por_especie": [], "top_rutas": [], "capturas_reales": 0}

    por_especie: dict = {}
    for r in rutas:
        esp = r.especie_objetivo or "DESCONOCIDA"
        if esp not in por_especie:
            por_especie[esp] = {"especie": esp, "rutas": 0, "km_total": 0, "carga_total": 0}
        por_especie[esp]["rutas"]      += 1
        por_especie[esp]["km_total"]   += r.distancia_total_km or 0
        por_especie[esp]["carga_total"] += r.carga_estimada_tm or 0

    top_rutas = sorted(rutas, key=lambda r: r.distancia_total_km or 0, reverse=True)[:5]
    capturas_reales = sum(1 for r in rutas if r.captura_real_tm is not None)

    return {
        "total":            len(rutas),
        "km_total":         round(sum(r.distancia_total_km or 0 for r in rutas), 1),
        "carga_total_tm":   round(sum(r.carga_estimada_tm or 0 for r in rutas), 1),
        "capturas_reales":  capturas_reales,
        "por_especie":      list(por_especie.values()),
        "top_rutas": [
            {
                "id":         r.id_ruta,
                "especie":    r.especie_objetivo,
                "distancia":  r.distancia_total_km,
                "carga":      r.carga_estimada_tm,
                "captura_real": r.captura_real_tm,
                "fecha":      r.fecha_calculo.isoformat() if r.fecha_calculo else None,
                "olas":       r.condicion_olas_m,
                "temp_mar":   r.temp_mar_c,
            }
            for r in top_rutas
        ],
        "ultimas_rutas": [
            {
                "id":         r.id_ruta,
                "especie":    r.especie_objetivo,
                "distancia":  r.distancia_total_km,
                "carga":      r.carga_estimada_tm,
                "captura_real": r.captura_real_tm,
                "fecha":      r.fecha_calculo.isoformat() if r.fecha_calculo else None,
                "olas":       r.condicion_olas_m,
                "temp_mar":   r.temp_mar_c,
            }
            for r in rutas[:20]
        ],
    }


@router.patch("/historial/{id_ruta}/captura")
async def reportar_captura_ruta(
    id_ruta: int,
    captura_tm: float,
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la captura real de una ruta existente."""
    result = await db.execute(
        select(HistorialRuta).where(HistorialRuta.id_ruta == id_ruta)
    )
    ruta = result.scalar_one_or_none()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    ruta.captura_real_tm = captura_tm
    await db.commit()
    return {"mensaje": "Captura registrada", "id_ruta": id_ruta, "captura_tm": captura_tm}