from pydantic import BaseModel
from typing import List, Optional

# --- ESQUEMAS DE AUTENTICACIÓN ---
class UsuarioLogin(BaseModel):
    username: str
    password: str

class UsuarioRegistro(BaseModel):
    username: str
    password: str
    nombre_completo: str
    id_embarcacion_asignada: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    nombre_usuario: str
    rol: str
    id_embarcacion: Optional[str] = None

# --- ESQUEMAS DE DATOS ---
class PuertoResponse(BaseModel):
    id: str
    nombre: str
    latitud: float
    longitud: float
    x: float
    y: float
    class Config: from_attributes = True

class BancoResponse(BaseModel):
    id: int
    latitud: float
    longitud: float
    toneladas: float
    x: float
    y: float
    class Config: from_attributes = True

class EmbarcacionResponse(BaseModel):
    id_embarcacion: str
    nombre: str
    capacidad_bodega: float
    velocidad_promedio: float
    consumo: float
    material: Optional[str] = "Desconocido"
    tripulacion: Optional[int] = 0
    anio_fabricacion: Optional[int] = 0
    estado: str
    progreso: int
    destino: str
    eta: str
    class Config: from_attributes = True

class EmbarcacionCreate(BaseModel):
    nombre: str
    capacidad_bodega: float
    velocidad_promedio: float
    consumo: float = 1.5
    material: str
    tripulacion: int
    anio_fabricacion: int

class KpiResponse(BaseModel):
    flota_activa: str
    operatividad: str
    pesca_dia: str
    ahorro: str
    alertas: int

# --- ESQUEMAS VRP (MODIFICADO) ---
class RutaRequest(BaseModel):
    id_embarcacion: str
    capacidad_actual: Optional[float] = None
    combustible_actual: Optional[float] = None
    velocidad_personalizada: Optional[float] = None
    # SOLO QUEDA PUERTO SALIDA (El retorno es automático)
    puerto_salida_id: str

class NodoRuta(BaseModel):
    id_nodo: str
    tipo: str
    latitud: float
    longitud: float
    carga_acumulada: float
    x: float
    y: float

class RutaResponse(BaseModel):
    id_embarcacion: str
    distancia_total_km: float
    carga_total_tm: float
    tiempo_estimado_horas: float
    secuencia_ruta: List[NodoRuta]
    mensaje: str
    resumen_texto: str 

class ChartData(BaseModel):
    label: str
    value: float
    color: Optional[str] = None

class TopBarco(BaseModel):
    ranking: int
    nombre: str
    captura_total: float
    eficiencia: float

class MaterialData(BaseModel):
    material: str
    cantidad: int
    porcentaje: float

class ReporteGeneral(BaseModel):
    tendencia_semanal: List[ChartData]
    estado_flota: List[ChartData]
    top_barcos: List[TopBarco]
    distribucion_materiales: List[MaterialData]
    total_toneladas_detectadas: float
    zonas_mas_activas: str
    ahorro_carbono: float
    flota_capacidad_total: float

# --- AGREGAR AL FINAL DE schemas.py ---
class EstadoUpdate(BaseModel):
    estado: str