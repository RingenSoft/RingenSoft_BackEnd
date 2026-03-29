from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
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
    id_usuario      = Column(Integer, primary_key=True, index=True)
    username        = Column(String(50), unique=True, index=True, nullable=False)
    password_hash   = Column(String(255), nullable=False)
    nombre_completo = Column(String(100))
    rol             = Column(String(20), default="PESCADOR")

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

    embarcacion = relationship("Embarcacion")
