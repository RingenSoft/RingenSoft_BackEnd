from fastapi import FastAPI, HTTPException, Depends, status
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
import pandas as pd
import numpy as np
import math
import os
from jose import jwt, JWTError

# Imports Locales
from . import models, schemas, database, auth

app = FastAPI(title="Ringensoft API Real", version="5.0.0 - Production Ready")

# --- SEGURIDAD ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(database.get_db)):
    try:
        payload = auth.decodificar_token(token)
        if payload is None:
            raise HTTPException(status_code=401, detail="Token inválido")
        username: str = payload.get("sub")
        user = db.query(models.Usuario).filter(models.Usuario.username == username).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return user
    except Exception as e:
        raise HTTPException(status_code=401, detail="Sesión expirada o inválida")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Crear tablas
models.Base.metadata.create_all(bind=database.engine)

# --- VARIABLES GLOBALES ---
df_bancos = pd.DataFrame()
df_puertos = pd.DataFrame()
matriz_distancias = {} 

# --- UTILITARIOS GEOESPACIALES ---
def map_gps_to_css(lat, lon):
    map_norte = -0.038
    map_sur = -18.350
    map_oeste = -81.331
    map_este = -68.653
    y = (lat - map_norte) / (map_sur - map_norte) * 100
    x = (lon - map_oeste) / (map_este - map_oeste) * 100
    return x, y 

def haversine(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def es_en_mar(lat, lon):
    if lat > -3.0 or lat < -19.0: return False
    lon_limite = ((lat + 3) / -1.5 - 80.5) - 0.8 
    return lon < lon_limite

def encontrar_archivo(nombre_parcial):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    rutas = [os.path.join(base_dir, 'dataset'), base_dir, os.path.join(base_dir, '..'), os.path.join(base_dir, '..', 'dataset')]
    for d in rutas:
        if os.path.exists(d):
            for f in os.listdir(d):
                if nombre_parcial.lower() in f.lower() and not f.startswith('~$'):
                    return os.path.join(d, f)
    return None

# --- CARGA DE DATOS ---
@app.on_event("startup")
def load_data():
    global df_bancos, df_puertos, matriz_distancias
    print("\n🔄 INICIANDO SISTEMA RINGENSOFT (CORE)...")

    # 1. BANCOS
    f_bancos = encontrar_archivo("bancos")
    if f_bancos:
        try:
            df = pd.read_csv(f_bancos) if f_bancos.endswith('.csv') else pd.read_excel(f_bancos)
            df.columns = [c.strip() for c in df.columns]
            cols = {c.lower(): c for c in df.columns}
            c_lat, c_lon = cols.get('latitud'), cols.get('longitud')
            if c_lat and c_lon:
                df[c_lat] = pd.to_numeric(df[c_lat], errors='coerce')
                df[c_lon] = pd.to_numeric(df[c_lon], errors='coerce')
                df = df.dropna(subset=[c_lat, c_lon])
                mask = df.apply(lambda r: es_en_mar(r[c_lat], r[c_lon]), axis=1)
                df_bancos = df[mask].copy()
                print(f"✅ DATASET: {len(df_bancos)} Bancos validados en MAR.")
        except Exception as e: print(f"❌ Error bancos: {e}")

    # 2. PUERTOS
    f_desc = encontrar_archivo("descargas")
    if f_desc:
        try:
            df_d = pd.read_csv(f_desc, sep=';', encoding='latin-1', on_bad_lines='skip')
            ptos_reales = {
                'CHIMBOTE': (-9.08, -78.59), 'CALLAO': (-12.05, -77.15), 'PISCO': (-13.70, -76.20),
                'PAITA': (-5.09, -81.11), 'ILO': (-17.64, -71.34), 'MATARANI': (-17.00, -72.10),
                'VEGUETA': (-11.02, -77.64), 'TAMBO DE MORA': (-13.4, -76.1), 'COISHCO': (-9.02, -78.61),
                'SAMANCO': (-9.25, -78.50), 'SUPE': (-10.80, -77.70), 'CHANCAY': (-11.56, -77.27),
                'MALABRIGO': (-7.70, -79.43), 'BAYOVAR': (-5.83, -81.05), 'CHAMA': (-9.08, -78.59)
            }
            lista = []
            nombres_procesados = set()
            for p in df_d['PUERTO'].unique():
                if isinstance(p, str):
                    p_limpio = p.strip().upper()
                    if p_limpio in ptos_reales and p_limpio not in nombres_procesados:
                        lat, lon = ptos_reales[p_limpio]
                        lista.append({'id': p_limpio, 'nombre': p_limpio, 'latitud': lat, 'longitud': lon})
                        nombres_procesados.add(p_limpio)
            if not lista:
                for k, v in ptos_reales.items():
                    lista.append({'id': k, 'nombre': k, 'latitud': v[0], 'longitud': v[1]})
            df_puertos = pd.DataFrame(lista)
            print(f"✅ DATASET: {len(df_puertos)} Puertos activos.")
        except: pass

    # 3. FLOTA (Seeder)
    db = database.SessionLocal()
    try:
        count_system = db.query(models.Embarcacion).filter(models.Embarcacion.id_embarcacion.like("SYSTEM%")).count()
        if count_system == 0:
            f_flota = encontrar_archivo("datos_embarcaciones")
            if f_flota:
                print("⚡ DB: Poblando flota inicial...")
                df_excel = pd.read_excel(f_flota, sheet_name=0)
                df_excel.columns = [c.strip() for c in df_excel.columns]
                df_excel = df_excel.fillna(0)
                cols = {c.lower(): c for c in df_excel.columns}
                batch = []
                for idx, r in df_excel.iterrows():
                    sys_id = f"SYSTEM-{idx:04d}"
                    nuevo = models.Embarcacion(
                        id_embarcacion=sys_id,
                        nombre=str(r.get(cols.get('tipo de casco'), f'Nave {idx}')),
                        capacidad_bodega=float(r.get(cols.get('capacidad de carga (tm)'), 0)),
                        velocidad_promedio=float(r.get(cols.get('velocidad promedio (nudos)'), 12)),
                        consumo_combustible=float(r.get(cols.get('consumo combustible (l/km)'), 1.5)),
                        material_casco=str(r.get(cols.get('material del casco'), 'ACERO')),
                        tripulacion_maxima=int(r.get(cols.get('tripulación máxima'), 10)),
                        anio_fabricacion=int(r.get(cols.get('año de fabricación'), 2010)),
                        owner_id=None
                    )
                    batch.append(nuevo)
                    if len(batch) >= 100:
                        db.bulk_save_objects(batch); db.commit(); batch = []
                if batch: db.bulk_save_objects(batch); db.commit()
    finally:
        db.close()

    # 4. MATRIZ DISTANCIAS
    if not df_bancos.empty:
        print("🧮 ALGORITMO: Calculando matriz de costos...")
        cols_b = {c.lower(): c for c in df_bancos.columns}
        c_id = cols_b.get('id banco', 'id')
        c_lat, c_lon = cols_b.get('latitud', 'latitud'), cols_b.get('longitud', 'longitud')
        muestra = df_bancos.head(300).to_dict('records')
        
        if not df_puertos.empty:
            for _, p in df_puertos.iterrows():
                muestra.append({c_id: p['id'], c_lat: p['latitud'], c_lon: p['longitud']})

        for i in range(len(muestra)):
            id_o = str(muestra[i].get(c_id))
            lat_o = muestra[i].get(c_lat); lon_o = muestra[i].get(c_lon)
            for j in range(i + 1, len(muestra)):
                id_d = str(muestra[j].get(c_id))
                lat_d = muestra[j].get(c_lat); lon_d = muestra[j].get(c_lon)
                d = haversine(lat_o, lon_o, lat_d, lon_d)
                matriz_distancias[(id_o, id_d)] = d
                matriz_distancias[(id_d, id_o)] = d

# --- ENDPOINTS ---
@app.post("/auth/registro", status_code=status.HTTP_201_CREATED)
def registrar_usuario(usuario: schemas.UsuarioRegistro, db: Session = Depends(database.get_db)):
    if db.query(models.Usuario).filter(models.Usuario.username == usuario.username).first():
        raise HTTPException(status_code=400, detail="Usuario ya existe")
    nuevo = models.Usuario(username=usuario.username, password_hash=auth.encriptar_password(usuario.password), nombre_completo=usuario.nombre_completo)
    db.add(nuevo); db.commit()
    return {"mensaje": "Usuario creado"}

@app.post("/auth/login", response_model=schemas.Token)
def login(usuario: schemas.UsuarioLogin, db: Session = Depends(database.get_db)):
    user = db.query(models.Usuario).filter(models.Usuario.username == usuario.username).first()
    if not user or not auth.verificar_password(usuario.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    return {"access_token": auth.crear_access_token({"sub": user.username}), "token_type": "bearer", "nombre_usuario": user.nombre_completo, "rol": user.rol}

@app.get("/puertos", response_model=List[schemas.PuertoResponse])
def get_puertos_api():
    res = []
    if not df_puertos.empty:
        for _, r in df_puertos.iterrows():
            x, y = map_gps_to_css(r['latitud'], r['longitud'])
            if -10 <= x <= 110 and -10 <= y <= 110:
                res.append({"id": str(r['id']), "nombre": r['nombre'], "latitud": r['latitud'], "longitud": r['longitud'], "x": x, "y": y})
    return res

@app.get("/bancos", response_model=List[schemas.BancoResponse])
def get_bancos_api():
    if df_bancos.empty: return []
    muestra = df_bancos.sample(n=min(500, len(df_bancos)))
    cols = {c.lower(): c for c in df_bancos.columns}
    res = []
    for _, r in muestra.iterrows():
        lat = r[cols.get('latitud')]; lon = r[cols.get('longitud')]
        x, y = map_gps_to_css(lat, lon)
        if 0 <= x <= 100 and 0 <= y <= 100:
            res.append({"id": int(r[cols.get('id banco', 'id')]), "latitud": lat, "longitud": lon, "toneladas": float(r[cols.get('toneladas estimadas', 'toneladas')]), "x": x, "y": y})
    return res

@app.get("/embarcaciones", response_model=List[schemas.EmbarcacionResponse])
def get_flota_api(current_user: models.Usuario = Depends(get_current_user), db: Session = Depends(database.get_db)):
    mis_barcos = db.query(models.Embarcacion).filter(models.Embarcacion.owner_id == current_user.id_usuario).all()
    res = []
    for b in mis_barcos:
        res.append({
            "id_embarcacion": b.id_embarcacion, "nombre": b.nombre,
            "capacidad_bodega": b.capacidad_bodega, "velocidad_promedio": b.velocidad_promedio,
            "consumo": b.consumo_combustible, "material": b.material_casco,
            "tripulacion": b.tripulacion_maxima, "anio_fabricacion": b.anio_fabricacion,
            "estado": b.estado, "progreso": 0, "destino": "-", "eta": "-"
        })
    return res

@app.post("/embarcaciones", response_model=schemas.EmbarcacionResponse)
def crear_embarcacion(barco: schemas.EmbarcacionCreate, current_user: models.Usuario = Depends(get_current_user), db: Session = Depends(database.get_db)):
    count = db.query(models.Embarcacion).filter(models.Embarcacion.owner_id == current_user.id_usuario).count()
    nuevo_id = f"U{current_user.id_usuario}-{count + 1:03d}"
    nuevo = models.Embarcacion(id_embarcacion=nuevo_id, nombre=barco.nombre, capacidad_bodega=barco.capacidad_bodega, velocidad_promedio=barco.velocidad_promedio, consumo_combustible=barco.consumo, material_casco=barco.material, tripulacion_maxima=barco.tripulacion, anio_fabricacion=barco.anio_fabricacion, owner_id=current_user.id_usuario, estado="EN_PUERTO")
    db.add(nuevo); db.commit(); db.refresh(nuevo)
    return {
        "id_embarcacion": nuevo.id_embarcacion, "nombre": nuevo.nombre,
        "capacidad_bodega": nuevo.capacidad_bodega, "velocidad_promedio": nuevo.velocidad_promedio,
        "consumo": nuevo.consumo_combustible, "material": nuevo.material_casco,
        "tripulacion": nuevo.tripulacion_maxima, "anio_fabricacion": nuevo.anio_fabricacion,
        "estado": "EN_PUERTO", "progreso": 0, "destino": "-", "eta": "-"
    }

# --- ALGORITMO VRP + FÍSICA DE CONSUMO ---
def optimizar_2opt(ruta, funcion_distancia):
    mejor_ruta = ruta
    mejor_distancia = sum(funcion_distancia(ruta[i], ruta[i+1]) for i in range(len(ruta)-1))
    mejorado = True
    while mejorado:
        mejorado = False
        for i in range(1, len(ruta) - 2):
            for j in range(i + 1, len(ruta) - 1):
                if j - i == 1: continue 
                nueva_ruta = ruta[:]
                nueva_ruta[i:j] = ruta[j-1:i-1:-1]
                # Recalcular distancia solo del tramo
                nueva_distancia = sum(funcion_distancia(nueva_ruta[k], nueva_ruta[k+1]) for k in range(len(nueva_ruta)-1))
                if nueva_distancia < mejor_distancia:
                    mejor_ruta = nueva_ruta
                    mejor_distancia = nueva_distancia
                    mejorado = True
        ruta = mejor_ruta
    return mejor_ruta, mejor_distancia

@app.post("/optimizar-ruta/", response_model=schemas.RutaResponse)
def calcular_ruta(req: schemas.RutaRequest, db: Session = Depends(database.get_db)):
    barco = db.query(models.Embarcacion).filter(models.Embarcacion.id_embarcacion == req.id_embarcacion).first()
    if not barco: raise HTTPException(status_code=404, detail="Barco no encontrado")

    # 1. Recuperar Variables Físicas
    cap_max = req.capacidad_actual if req.capacidad_actual else barco.capacidad_bodega
    vel = req.velocidad_personalizada if req.velocidad_personalizada else barco.velocidad_promedio
    consumo_base = barco.consumo_combustible
    material = barco.material_casco.upper() if barco.material_casco else "ACERO"
    tripulacion = barco.tripulacion_maxima or 10

    # 2. Factores de Influencia
    factor_material = 1.0
    if "FIBRA" in material: factor_material = 0.90 
    elif "MADERA" in material: factor_material = 0.95
    elif "ALUMINIO" in material: factor_material = 0.92
    
    factor_tripulacion = 1.0 + (tripulacion * 0.005) 

    # 3. CONFIGURACIÓN DE PUERTOS (CICLO CERRADO)
    # Buscamos el puerto de salida
    pto_salida = df_puertos[df_puertos['id'] == req.puerto_salida_id].iloc[0]
    
    # LÓGICA DE RETORNO AUTOMÁTICO: El destino es el mismo origen
    pto_llegada = pto_salida 
    
    nodo_inicio = {"id": req.puerto_salida_id, "tipo": "PUERTO", "lat": pto_salida['latitud'], "lon": pto_salida['longitud'], "toneladas": 0}
    nodo_final = {"id": req.puerto_salida_id, "tipo": "PUERTO", "lat": pto_salida['latitud'], "lon": pto_salida['longitud'], "toneladas": 0}
    
    # 4. Candidatos (Bancos de Pesca)
    cols_b = {c.lower(): c for c in df_bancos.columns}
    candidatos = []
    # Aumentamos un poco la muestra para dar más opciones
    for _, row in df_bancos.head(200).iterrows(): 
        candidatos.append({
            "id": str(row[cols_b.get('id banco')]), "tipo": "BANCO",
            "lat": row[cols_b.get('latitud')], "lon": row[cols_b.get('longitud')],
            "toneladas": float(row[cols_b.get('toneladas estimadas')])
        })

    # FASE 1: GREEDY
    ruta_actual = [nodo_inicio]
    carga_actual = 0
    visitados = set()
    
    while carga_actual < cap_max:
        actual = ruta_actual[-1]
        mejor_cand = None
        min_dist = float('inf')
        
        for cand in candidatos:
            if cand['id'] in visitados: continue
            
            d = matriz_distancias.get((str(actual['id']), cand['id']))
            if d is None: d = haversine(actual['lat'], actual['lon'], cand['lat'], cand['lon'])
            
            if d < min_dist:
                min_dist = d
                mejor_cand = cand
        
        if mejor_cand is None or min_dist > 600: break
        
        pesca = min(mejor_cand['toneladas'], cap_max - carga_actual)
        if pesca <= 0: break 
        
        cand_copy = mejor_cand.copy()
        cand_copy['carga_recogida'] = pesca
        ruta_actual.append(cand_copy)
        visitados.add(mejor_cand['id'])
        carga_actual += pesca
        
        if carga_actual >= cap_max: break

    # AGREGAR NODO FINAL (RETORNO A CASA)
    ruta_actual.append(nodo_final)

    # FASE 2: 2-OPT
    def get_dist_func(n1, n2):
        d = matriz_distancias.get((str(n1['id']), str(n2['id'])))
        return d if d else haversine(n1['lat'], n1['lon'], n2['lat'], n2['lon'])

    if len(ruta_actual) > 3:
        ruta_optima, _ = optimizar_2opt(ruta_actual, get_dist_func)
        # Corrección simple si el 2-opt invierte el inicio/fin en ciclo cerrado
        if ruta_optima[0]['id'] != nodo_inicio['id']:
             ruta_optima = ruta_actual 
    else:
        ruta_optima = ruta_actual

    # FASE 3: CÁLCULO CONSUMO
    secuencia_ruta = []
    carga_acum = 0
    distancia_total = 0
    consumo_total = 0
    
    for i, nodo in enumerate(ruta_optima):
        recogida = nodo.get('carga_recogida', 0)
        carga_acum += recogida
        
        x, y = map_gps_to_css(nodo['lat'], nodo['lon'])
        secuencia_ruta.append({
            "id_nodo": f"{nodo['id']}", "tipo": nodo['tipo'],
            "latitud": nodo['lat'], "longitud": nodo['lon'],
            "carga_acumulada": round(carga_acum, 2), "x": x, "y": y
        })
        
        if i < len(ruta_optima) - 1:
            siguiente = ruta_optima[i+1]
            dist = get_dist_func(nodo, siguiente)
            distancia_total += dist
            
            factor_carga = 1.0 + (0.5 * (carga_acum / cap_max))
            consumo_tramo = dist * consumo_base * factor_material * factor_tripulacion * factor_carga
            consumo_total += consumo_tramo

    tiempo_hrs = distancia_total / (vel * 1.852)
    
    resumen = (
        f"OPERACIÓN CICLO CERRADO\n"
        f"• Puerto Base: {pto_salida['nombre']} \n"
        f"• Nave: {barco.nombre} ({material}) \n"
        f"• Carga Obtenida: {round(carga_acum, 2)} TM \n"
        f"• Consumo Total: {round(consumo_total, 1)} Galones\n"
        f"• Eficiencia: {round(distancia_total/len(ruta_optima), 1)} km/tramo"
    )

    return {
        "id_embarcacion": req.id_embarcacion,
        "distancia_total_km": round(distancia_total, 2),
        "carga_total_tm": round(carga_acum, 2),
        "tiempo_estimado_horas": round(tiempo_hrs, 2),
        "secuencia_ruta": secuencia_ruta,
        "mensaje": "Ruta de retorno optimizada.",
        "resumen_texto": resumen
    }
# --- DASHBOARD ---
@app.get("/kpis", response_model=schemas.KpiResponse)
def get_kpis_api(db: Session = Depends(database.get_db)):
    total_barcos = db.query(models.Embarcacion).count()
    activos = db.query(models.Embarcacion).filter(models.Embarcacion.estado != "MANTENIMIENTO").count()
    capacidad_total = db.query(func.sum(models.Embarcacion.capacidad_bodega)).scalar() or 0
    return { "flota_activa": f"{activos} / {total_barcos}", "operatividad": f"{round((activos/total_barcos)*100, 1) if total_barcos > 0 else 0}%", "pesca_dia": f"{capacidad_total:,.0f} TM", "ahorro": "12.5%", "alertas": 0 }

@app.get("/reportes/dashboard", response_model=schemas.ReporteGeneral)
def get_reportes_dashboard(db: Session = Depends(database.get_db)):
    estados = db.query(models.Embarcacion.estado, func.count(models.Embarcacion.estado)).group_by(models.Embarcacion.estado).all()
    dict_estados = {e[0]: e[1] for e in estados}
    chart_flota = [
        schemas.ChartData(label="En Ruta", value=dict_estados.get("EN_RUTA", 0), color="#10b981"),
        schemas.ChartData(label="Pescando", value=dict_estados.get("EN_ALTAMAR", 0), color="#3b82f6"),
        schemas.ChartData(label="En Puerto", value=dict_estados.get("EN_PUERTO", 0), color="#94a3b8"),
        schemas.ChartData(label="Mantenimiento", value=dict_estados.get("MANTENIMIENTO", 0), color="#ef4444")
    ]
    top_db = db.query(models.Embarcacion).order_by(models.Embarcacion.capacidad_bodega.desc()).limit(5).all()
    top_barcos = [schemas.TopBarco(ranking=i+1, nombre=b.nombre, captura_total=b.capacidad_bodega * 4.5, eficiencia=95.0) for i, b in enumerate(top_db)]
    
    total_naves = db.query(models.Embarcacion).count() or 1
    materiales = db.query(models.Embarcacion.material_casco, func.count(models.Embarcacion.material_casco)).group_by(models.Embarcacion.material_casco).all()
    dist_materiales = [schemas.MaterialData(material=m[0] if m[0] else "Desconocido", cantidad=m[1], porcentaje=round((m[1]/total_naves)*100, 1)) for m in materiales]

    zona_activa = "Sin datos"
    total_biomasa = 0
    if not df_bancos.empty:
        col_ton = {c.lower(): c for c in df_bancos.columns}.get('toneladas estimadas')
        col_lat = {c.lower(): c for c in df_bancos.columns}.get('latitud')
        if col_ton and col_lat:
            total_biomasa = df_bancos[col_ton].sum()
            norte = df_bancos[df_bancos[col_lat] > -9][col_ton].sum()
            centro = df_bancos[(df_bancos[col_lat] <= -9) & (df_bancos[col_lat] >= -14)][col_ton].sum()
            sur = df_bancos[df_bancos[col_lat] < -14][col_ton].sum()
            if norte >= centro and norte >= sur: zona_activa = "Norte (Paita-Chimbote)"
            elif centro >= norte and centro >= sur: zona_activa = "Centro (Callao-Pisco)"
            else: zona_activa = "Sur (Ilo-Matarani)"

    cap_operativa = db.query(func.sum(models.Embarcacion.capacidad_bodega)).scalar() or 0
    dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    chart_semanal = [schemas.ChartData(label=dia, value=round(cap_operativa * (0.6 + (i%3)*0.15))) for i, dia in enumerate(dias)]

    return {
        "tendencia_semanal": chart_semanal, "estado_flota": chart_flota, "top_barcos": top_barcos,
        "distribucion_materiales": dist_materiales, "total_toneladas_detectadas": round(total_biomasa, 2),
        "zonas_mas_activas": zona_activa, "ahorro_carbono": round(cap_operativa * 0.85, 2), "flota_capacidad_total": cap_operativa
    }