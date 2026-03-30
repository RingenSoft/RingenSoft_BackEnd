

import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
import auth
from models import Usuario, Embarcacion, HistorialRuta, Avistamiento, PlanViaje, Mantenimiento, MensajeComunidad
from services.weather_service import obtener_condiciones_mar
from services.ocean_service import obtener_datos_zona
from services.fish_grid import (
    generar_grilla, filtrar_por_radio,
    calcular_radio_km, get_puerto, PUERTOS_PERU
)
from services.fish_score import calcular_scores_zona, seleccionar_mejores_zonas
from services.route_optimizer import optimizar_ruta

router = APIRouter(prefix="/api/v2", tags=["v2"])
limiter = Limiter(key_func=get_remote_address)


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


@router.get("/pronostico")
async def get_pronostico(
    lat: float = Query(..., example=-9.07),
    lon: float = Query(..., example=-78.59),
):
    """Pronóstico horario de olas y viento para las próximas 48h + mejor ventana para salir."""
    from services.weather_service import obtener_pronostico_48h
    try:
        return await obtener_pronostico_48h(lat, lon)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ruta-optima")
@limiter.limit("10/minute")
async def post_ruta_optima(request: Request, req: RutaRequest, db: AsyncSession = Depends(get_db)):
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
            zonas_nodos = [n for n in resultado.get("ruta", []) if n["tipo"] == "ZONA_PESCA"]
            clorofila_vals = [n["clorofila"] for n in zonas_nodos if n.get("clorofila") is not None]
            nueva_ruta = HistorialRuta(
                id_embarcacion      = req.id_embarcacion,
                distancia_total_km  = resultado["distancia_total_km"],
                combustible_usado   = resultado["combustible_usado_l"],
                carga_estimada_tm   = resultado["carga_estimada_tm"],
                condicion_olas_m    = altura_olas,
                condicion_viento    = clima["viento"]["velocidad_kmh"],
                temp_mar_c          = scored[0].get("temperatura_c") if scored else None,
                especie_objetivo    = especie,
                fish_score_promedio = resultado.get("fish_score_promedio"),
                zonas_visitadas_num = resultado.get("zonas_visitadas"),
                tiempo_total_horas  = resultado.get("tiempo_total_horas"),
                clorofila_promedio  = round(sum(clorofila_vals) / len(clorofila_vals), 4) if clorofila_vals else None,
                ruta_json           = resultado.get("ruta"),
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


@router.post("/rutas-comparadas")
@limiter.limit("5/minute")
async def post_rutas_comparadas(request: Request, req: RutaRequest, db: AsyncSession = Depends(get_db)):
    """
    Calcula 3 rutas alternativas (equilibrada, máxima captura, mínimo combustible)
    para que el capitán elija la que mejor se ajuste a sus objetivos.
    """
    puerto = get_puerto(req.id_puerto)
    if not puerto:
        raise HTTPException(status_code=404, detail=f"Puerto '{req.id_puerto}' no encontrado.")

    especie = req.especie.upper()
    if especie not in ["ANCHOVETA", "BONITO", "CABALLA", "JUREL"]:
        raise HTTPException(status_code=400, detail="Especie inválida.")

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
        clima = await obtener_condiciones_mar(puerto["lat"], puerto["lon"])
        if not clima["navegacion_segura"]:
            return {"status": "BLOQUEADO", "alerta": clima["alerta"], "rutas": None}

        radio_km = calcular_radio_km(
            embarcacion["velocidad_promedio"],
            embarcacion["autonomia_horas"],
            combustible_pct,
        )
        grilla   = generar_grilla()
        puntos   = filtrar_por_radio(grilla, puerto["lat"], puerto["lon"], radio_km)
        muestra  = puntos[:20]
        scored   = await calcular_scores_zona(muestra, especie, puerto["lat"], puerto["lon"])
        mejores  = seleccionar_mejores_zonas(scored, top_n=req.top_zonas, score_minimo=20.0) or scored[:3]

        altura_olas = clima["mar"]["altura_olas_m"]

        # Intentar obtener corrientes para las zonas (sin bloquear si falla)
        corrientes = {}
        try:
            from services.currents_service import obtener_corriente
            tasks = {(z["lat"], z["lon"]): obtener_corriente(z["lat"], z["lon"]) for z in mejores}
            import asyncio as _asyncio
            resultados_cor = await _asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, res in zip(tasks.keys(), resultados_cor):
                if not isinstance(res, Exception):
                    corrientes[key] = res
        except Exception:
            pass

        rutas = []
        for modo in ["equilibrado", "max_captura", "min_combustible"]:
            r = optimizar_ruta(
                puerto=puerto,
                zonas=mejores,
                embarcacion=embarcacion,
                combustible_pct=combustible_pct,
                especie=especie,
                altura_olas=altura_olas,
                modo=modo,
                corrientes=corrientes,
            )
            rutas.append(r)

        return {
            "status":            "OK",
            "alerta":            clima["alerta"],
            "radio_operacion_km": radio_km,
            "puntos_evaluados":  len(muestra),
            "rutas":             rutas,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- ENDPOINTS ML ---

@router.post("/ml/entrenar")
async def ml_entrenar(db: AsyncSession = Depends(get_db)):
    """Entrena o re-entrena el modelo ML con los datos históricos disponibles."""
    try:
        from services.ml_service import entrenar_modelo
        resultado = await entrenar_modelo(db)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ml/estado")
async def ml_estado():
    """Retorna el estado actual del modelo ML (samples, R², fecha de entrenamiento)."""
    try:
        from services.ml_service import estado_modelo
        return estado_modelo()
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
                "id_embarcacion": r.id_embarcacion,
                "fish_score":     r.fish_score_promedio,
                "tiempo_horas":   r.tiempo_total_horas,
            }
            for r in rutas
        ]
    }


@router.get("/historial/pendientes")
async def get_historial_pendientes(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rutas calculadas que aún no tienen captura real reportada."""
    result = await db.execute(
        select(HistorialRuta)
        .where(HistorialRuta.captura_real_tm.is_(None))
        .where(HistorialRuta.carga_estimada_tm.isnot(None))
        .order_by(HistorialRuta.fecha_calculo.desc())
        .limit(20)
    )
    rutas = result.scalars().all()
    return {
        "total": len(rutas),
        "rutas": [
            {
                "id":             r.id_ruta,
                "fecha":          r.fecha_calculo.isoformat() if r.fecha_calculo else None,
                "especie":        r.especie_objetivo or "—",
                "distancia_km":   r.distancia_total_km,
                "carga_estimada": r.carga_estimada_tm,
                "id_embarcacion": r.id_embarcacion or "—",
                "fish_score":     r.fish_score_promedio,
                "tiempo_horas":   r.tiempo_total_horas,
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
    from services.fish_grid import generar_grilla, filtrar_por_radio, get_puerto
    from services.fish_score import calcular_scores_zona

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


class EstadoUpdate(BaseModel):
    estado: str

@router.patch("/embarcaciones/{id_embarcacion}/estado")
async def cambiar_estado_embarcacion(
    id_embarcacion: str,
    req: EstadoUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Updates vessel status (EN_PUERTO / EN_MAR / MANTENIMIENTO)."""
    result = await db.execute(
        select(Embarcacion).where(
            Embarcacion.id_embarcacion == id_embarcacion,
            Embarcacion.owner_id == current_user.id_usuario
        )
    )
    barco = result.scalar_one_or_none()
    if not barco:
        raise HTTPException(status_code=404, detail="Embarcación no encontrada")
    barco.estado = req.estado
    await db.commit()
    return {"id_embarcacion": barco.id_embarcacion, "estado": barco.estado}


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


# --- AVISTAMIENTOS ---
class AvistamientoCreate(BaseModel):
    especie:     str
    zona:        str
    descripcion: str

@router.get("/avistamientos")
async def get_avistamientos(db: AsyncSession = Depends(get_db)):
    """Lista los últimos 50 avistamientos reportados por la comunidad."""
    result = await db.execute(
        select(Avistamiento).order_by(Avistamiento.fecha.desc()).limit(50)
    )
    items = result.scalars().all()
    return [
        {
            "id":          a.id,
            "especie":     a.especie,
            "zona":        a.zona,
            "descripcion": a.descripcion,
            "fecha":       a.fecha.isoformat() if a.fecha else None,
            "votos":       a.votos,
        }
        for a in items
    ]

@router.post("/avistamientos")
async def crear_avistamiento(
    req: AvistamientoCreate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Publica un nuevo avistamiento de cardumen."""
    nuevo = Avistamiento(
        especie     = req.especie,
        zona        = req.zona,
        descripcion = req.descripcion,
        id_usuario  = current_user.id_usuario,
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return {
        "id":          nuevo.id,
        "especie":     nuevo.especie,
        "zona":        nuevo.zona,
        "descripcion": nuevo.descripcion,
        "fecha":       nuevo.fecha.isoformat() if nuevo.fecha else None,
        "votos":       nuevo.votos,
    }

@router.patch("/avistamientos/{id_avistamiento}/votar")
async def votar_avistamiento(
    id_avistamiento: int,
    db: AsyncSession = Depends(get_db)
):
    """Incrementa el contador de votos de un avistamiento."""
    result = await db.execute(
        select(Avistamiento).where(Avistamiento.id == id_avistamiento)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Avistamiento no encontrado")
    item.votos += 1
    await db.commit()
    return {"id": item.id, "votos": item.votos}


# --- PLANES DE VIAJE ---
class PlanCreate(BaseModel):
    nombre_viaje:    str
    puerto_id:       str
    especie:         str
    fecha_salida:    str
    hora_salida:     str = "06:00"
    id_embarcacion:  Optional[str] = None
    combustible_pct: float = 0.9
    notas:           Optional[str] = None
    condiciones_json: Optional[str] = None

class PlanEstadoUpdate(BaseModel):
    estado: str

def _plan_to_dict(p: PlanViaje) -> dict:
    return {
        "id":             p.id,
        "nombre_viaje":   p.nombre_viaje,
        "puerto_id":      p.puerto_id,
        "especie":        p.especie,
        "fecha_salida":   p.fecha_salida,
        "hora_salida":    p.hora_salida,
        "id_embarcacion": p.id_embarcacion,
        "combustible_pct": p.combustible_pct,
        "notas":          p.notas,
        "estado":         p.estado,
        "condiciones_json": p.condiciones_json,
        "fecha_creado":   p.fecha_creado.isoformat() if p.fecha_creado else None,
    }

@router.get("/planes")
async def get_planes(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista los planes de viaje del usuario autenticado."""
    result = await db.execute(
        select(PlanViaje)
        .where(PlanViaje.id_usuario == current_user.id_usuario)
        .order_by(PlanViaje.fecha_creado.desc())
    )
    return [_plan_to_dict(p) for p in result.scalars().all()]

@router.post("/planes")
async def crear_plan(
    req: PlanCreate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo plan de viaje."""
    nuevo = PlanViaje(
        nombre_viaje     = req.nombre_viaje,
        puerto_id        = req.puerto_id,
        especie          = req.especie,
        fecha_salida     = req.fecha_salida,
        hora_salida      = req.hora_salida,
        id_embarcacion   = req.id_embarcacion,
        combustible_pct  = req.combustible_pct,
        notas            = req.notas,
        condiciones_json = req.condiciones_json,
        id_usuario       = current_user.id_usuario,
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return _plan_to_dict(nuevo)

@router.patch("/planes/{id_plan}/estado")
async def actualizar_estado_plan(
    id_plan: int,
    req: PlanEstadoUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el estado de un plan (PLANIFICADO / COMPLETADO / CANCELADO)."""
    result = await db.execute(
        select(PlanViaje).where(
            PlanViaje.id == id_plan,
            PlanViaje.id_usuario == current_user.id_usuario
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")
    plan.estado = req.estado
    await db.commit()
    return {"id": plan.id, "estado": plan.estado}

@router.delete("/planes/{id_plan}")
async def eliminar_plan(
    id_plan: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un plan de viaje."""
    result = await db.execute(
        select(PlanViaje).where(
            PlanViaje.id == id_plan,
            PlanViaje.id_usuario == current_user.id_usuario
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")
    await db.delete(plan)
    await db.commit()
    return {"mensaje": "Plan eliminado", "id": id_plan}


# --- MANTENIMIENTOS ---
class MantenimientoCreate(BaseModel):
    id_embarcacion:   str
    nombre_embarcacion: Optional[str] = None
    fecha:            str
    tipo:             str = "PREVENTIVO"
    descripcion:      str
    costo:            float = 0.0
    proxima_revision: Optional[str] = None

@router.get("/mantenimientos")
async def get_mantenimientos(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista los registros de mantenimiento del usuario."""
    result = await db.execute(
        select(Mantenimiento)
        .where(Mantenimiento.id_usuario == current_user.id_usuario)
        .order_by(Mantenimiento.fecha_creado.desc())
    )
    items = result.scalars().all()
    return [
        {
            "id":                 m.id,
            "id_embarcacion":     m.id_embarcacion,
            "nombre_embarcacion": m.nombre_embarcacion,
            "fecha":              m.fecha,
            "tipo":               m.tipo,
            "descripcion":        m.descripcion,
            "costo":              m.costo,
            "proxima_revision":   m.proxima_revision,
        }
        for m in items
    ]

@router.post("/mantenimientos")
async def crear_mantenimiento(
    req: MantenimientoCreate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Registra un nuevo mantenimiento para una embarcación."""
    nuevo = Mantenimiento(
        id_embarcacion     = req.id_embarcacion,
        nombre_embarcacion = req.nombre_embarcacion,
        fecha              = req.fecha,
        tipo               = req.tipo,
        descripcion        = req.descripcion,
        costo              = req.costo,
        proxima_revision   = req.proxima_revision,
        id_usuario         = current_user.id_usuario,
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return {"id": nuevo.id, "mensaje": "Mantenimiento registrado"}

@router.delete("/mantenimientos/{id_mant}")
async def eliminar_mantenimiento(
    id_mant: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un registro de mantenimiento."""
    result = await db.execute(
        select(Mantenimiento).where(
            Mantenimiento.id == id_mant,
            Mantenimiento.id_usuario == current_user.id_usuario
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    await db.delete(item)
    await db.commit()
    return {"mensaje": "Mantenimiento eliminado", "id": id_mant}


# --- PERFIL EXTENDIDO ---
class PerfilUpdate(BaseModel):
    zona_habitual:    Optional[str]   = None
    tipo_pescador:    Optional[str]   = None
    anos_experiencia: Optional[int]   = None
    licencia_pesca:   Optional[str]   = None
    telefono:         Optional[str]   = None

@router.get("/perfil")
async def get_perfil(
    current_user: Usuario = Depends(get_current_user),
):
    """Retorna el perfil extendido del usuario autenticado."""
    return {
        "id_usuario":       current_user.id_usuario,
        "username":         current_user.username,
        "nombre_completo":  current_user.nombre_completo,
        "rol":              current_user.rol,
        "zona_habitual":    current_user.zona_habitual,
        "tipo_pescador":    current_user.tipo_pescador,
        "anos_experiencia": current_user.anos_experiencia,
        "licencia_pesca":   current_user.licencia_pesca,
        "telefono":         current_user.telefono,
    }

@router.patch("/perfil")
async def actualizar_perfil(
    req: PerfilUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza los datos extendidos del perfil del usuario."""
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
    await db.commit()
    return {"mensaje": "Perfil actualizado correctamente"}


# --- RANKINGS ---
@router.get("/rankings")
async def get_rankings(db: AsyncSession = Depends(get_db)):
    """Top pescadores por captura real total, rutas y km navegados."""
    result = await db.execute(
        select(HistorialRuta, Usuario)
        .join(Embarcacion, HistorialRuta.id_embarcacion == Embarcacion.id_embarcacion, isouter=True)
        .join(Usuario, Embarcacion.owner_id == Usuario.id_usuario, isouter=True)
        .where(HistorialRuta.captura_real_tm.isnot(None))
    )
    rows = result.all()

    stats: dict = {}
    for ruta, usuario in rows:
        uid = usuario.id_usuario if usuario else 0
        nombre = usuario.nombre_completo if usuario else "Anónimo"
        if uid not in stats:
            stats[uid] = {
                "id_usuario":      uid,
                "nombre":          nombre,
                "total_capturas":  0.0,
                "total_rutas":     0,
                "total_km":        0.0,
                "mejor_captura":   0.0,
            }
        stats[uid]["total_capturas"] += ruta.captura_real_tm or 0
        stats[uid]["total_rutas"]    += 1
        stats[uid]["total_km"]       += ruta.distancia_total_km or 0
        if (ruta.captura_real_tm or 0) > stats[uid]["mejor_captura"]:
            stats[uid]["mejor_captura"] = ruta.captura_real_tm or 0

    ranking = sorted(stats.values(), key=lambda x: x["total_capturas"], reverse=True)[:10]
    for i, r in enumerate(ranking):
        r["posicion"] = i + 1
        r["total_capturas"] = round(r["total_capturas"], 2)
        r["total_km"]       = round(r["total_km"], 1)

    return {"ranking": ranking}


# --- CHAT / MENSAJES COMUNIDAD ---
class MensajeCreate(BaseModel):
    texto: str
    tipo:  str = "GENERAL"   # GENERAL | ALERTA | PREGUNTA | OFERTA

@router.get("/mensajes")
async def get_mensajes(db: AsyncSession = Depends(get_db)):
    """Últimos 50 mensajes del chat comunitario."""
    result = await db.execute(
        select(MensajeComunidad).order_by(MensajeComunidad.fecha.desc()).limit(50)
    )
    items = result.scalars().all()
    return [
        {
            "id":     m.id,
            "texto":  m.texto,
            "tipo":   m.tipo,
            "autor":  m.autor,
            "fecha":  m.fecha.isoformat() if m.fecha else None,
        }
        for m in reversed(items)
    ]

@router.post("/mensajes")
async def crear_mensaje(
    req: MensajeCreate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Publica un mensaje en el chat comunitario."""
    nuevo = MensajeComunidad(
        texto      = req.texto.strip()[:500],
        tipo       = req.tipo,
        autor      = current_user.nombre_completo or current_user.username,
        id_usuario = current_user.id_usuario,
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return {
        "id":    nuevo.id,
        "texto": nuevo.texto,
        "tipo":  nuevo.tipo,
        "autor": nuevo.autor,
        "fecha": nuevo.fecha.isoformat() if nuevo.fecha else None,
    }