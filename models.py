from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base

# 1. PUERTOS
class Puerto(Base):
    __tablename__ = "puertos"
    id_puerto     = Column(String(20), primary_key=True, index=True)
    nombre        = Column(String(100), nullable=False)
    latitud       = Column(Float, nullable=False)
    longitud      = Column(Float, nullable=False)
    tipo          = Column(String(50), default="ARTESANAL")
    profundidad_m = Column(Float, default=5.0)

    embarcaciones = relationship("Embarcacion", back_populates="puerto_base")

# 2. USUARIOS
class Usuario(Base):
    __tablename__ = "usuarios"
    id_usuario        = Column(Integer, primary_key=True, index=True)
    username          = Column(String(50), unique=True, index=True, nullable=False)
    password_hash     = Column(String(255), nullable=False)
    nombre_completo   = Column(String(100))
    rol               = Column(String(20), default="PESCADOR")
    zona_habitual     = Column(String(20), nullable=True)
    tipo_pescador     = Column(String(50), nullable=True)
    anos_experiencia  = Column(Integer, nullable=True)
    licencia_pesca    = Column(String(100), nullable=True)
    telefono          = Column(String(20), nullable=True)

    mis_embarcaciones = relationship("Embarcacion", back_populates="owner")

# 3. EMBARCACIONES
class Embarcacion(Base):
    __tablename__ = "embarcaciones"
    id_embarcacion    = Column(String(50), primary_key=True, index=True)
    nombre            = Column(String(100))
    capacidad_bodega  = Column(Float, nullable=False)
    velocidad_promedio= Column(Float, default=10.0)   # nudos
    consumo_hora      = Column(Float, default=20.0)   # litros/hora
    autonomia_horas   = Column(Float, default=24.0)   # horas con tanque lleno
    tipo_motor        = Column(String(50), default="DIESEL")
    material_casco    = Column(String(50), default="FIBRA")
    tripulacion_max   = Column(Integer, default=6)
    anio_fabricacion  = Column(Integer, default=2015)
    estado            = Column(String(50), default="EN_PUERTO")

    puerto_base_id = Column(String(20), ForeignKey("puertos.id_puerto"), nullable=True)
    puerto_base    = relationship("Puerto", back_populates="embarcaciones")
    owner_id       = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
    owner          = relationship("Usuario", back_populates="mis_embarcaciones")

# 4. HISTORIAL DE RUTAS (enriquecido)
class HistorialRuta(Base):
    __tablename__ = "historial_rutas"
    id_ruta            = Column(Integer, primary_key=True, index=True)
    id_embarcacion = Column(String(50), ForeignKey("embarcaciones.id_embarcacion"), nullable=True)
    fecha_calculo      = Column(DateTime(timezone=True), server_default=func.now())
    distancia_total_km = Column(Float)
    combustible_usado  = Column(Float)   # litros estimados
    carga_estimada_tm  = Column(Float)   # toneladas estimadas según FishScore
    captura_real_tm    = Column(Float, nullable=True)  # reportado al regresar
    condicion_olas_m   = Column(Float, nullable=True)  # altura olas al salir
    condicion_viento   = Column(Float, nullable=True)  # km/h al salir
    temp_mar_c         = Column(Float, nullable=True)  # °C al salir
    especie_objetivo   = Column(String(50), nullable=True)
    # Campos enriquecidos para ML
    fish_score_promedio  = Column(Float, nullable=True)
    zonas_visitadas_num  = Column(Integer, nullable=True)
    tiempo_total_horas   = Column(Float, nullable=True)
    clorofila_promedio   = Column(Float, nullable=True)
    ruta_json            = Column(JSON, nullable=True)   # lista completa de nodos

    embarcacion = relationship("Embarcacion")


# 5. AVISTAMIENTOS
class Avistamiento(Base):
    __tablename__ = "avistamientos"
    id          = Column(Integer, primary_key=True, index=True)
    especie     = Column(String(50), nullable=False)
    zona        = Column(String(100), nullable=False)
    descripcion = Column(String(500), nullable=False)
    fecha       = Column(DateTime(timezone=True), server_default=func.now())
    votos       = Column(Integer, default=0)
    id_usuario  = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)


# 6. MENSAJES COMUNIDAD
class MensajeComunidad(Base):
    __tablename__ = "mensajes_comunidad"
    id         = Column(Integer, primary_key=True, index=True)
    texto      = Column(String(500), nullable=False)
    tipo       = Column(String(20), default="GENERAL")
    autor      = Column(String(100), nullable=False)
    id_usuario = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
    fecha      = Column(DateTime(timezone=True), server_default=func.now())


# 7. MANTENIMIENTOS
class Mantenimiento(Base):
    __tablename__ = "mantenimientos"
    id                  = Column(Integer, primary_key=True, index=True)
    id_embarcacion      = Column(String(50), ForeignKey("embarcaciones.id_embarcacion"), nullable=False)
    nombre_embarcacion  = Column(String(100), nullable=True)
    fecha               = Column(String(20), nullable=False)
    tipo                = Column(String(50), default="PREVENTIVO")
    descripcion         = Column(String(500), nullable=False)
    costo               = Column(Float, default=0.0)
    proxima_revision    = Column(String(20), nullable=True)
    id_usuario          = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
    fecha_creado        = Column(DateTime(timezone=True), server_default=func.now())

    embarcacion = relationship("Embarcacion")


# 7. PLANES DE VIAJE
class PlanViaje(Base):
    __tablename__ = "planes_viaje"
    id               = Column(Integer, primary_key=True, index=True)
    nombre_viaje     = Column(String(200), nullable=False)
    puerto_id        = Column(String(20), nullable=False)
    especie          = Column(String(50), nullable=False)
    fecha_salida     = Column(String(20), nullable=False)
    hora_salida      = Column(String(10), default="06:00")
    id_embarcacion   = Column(String(50), nullable=True)
    combustible_pct  = Column(Float, default=0.9)
    notas            = Column(String(500), nullable=True)
    estado           = Column(String(50), default="PLANIFICADO")
    condiciones_json = Column(String(2000), nullable=True)
    fecha_creado     = Column(DateTime(timezone=True), server_default=func.now())
    id_usuario       = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
