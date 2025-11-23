from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base

# 1. TABLA PUERTOS
class Puerto(Base):
    __tablename__ = "puertos"
    id_puerto = Column(String(20), primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    latitud = Column(Float)
    longitud = Column(Float)
    # Relación inversa
    embarcaciones = relationship("Embarcacion", back_populates="puerto_base")

# 2. TABLA BANCOS DE PESCA
class BancoPesca(Base):
    __tablename__ = "bancos_pesca"
    id_banco = Column(Integer, primary_key=True, index=True)
    latitud = Column(Float, nullable=False)
    longitud = Column(Float, nullable=False)
    toneladas_estimadas = Column(Float)
    profundidad = Column(Float)
    temperatura_agua = Column(Float)
    fecha_avistamiento = Column(Date)
    estado = Column(String(50), default='DISPONIBLE')

# 3. TABLA USUARIOS (Pescadores)
class Usuario(Base):
    __tablename__ = "usuarios"
    id_usuario = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nombre_completo = Column(String(100))
    rol = Column(String(20), default="PESCADOR")
    
    # Relación con barcos (Un usuario tiene muchos barcos)
    mis_embarcaciones = relationship("Embarcacion", back_populates="owner")

# 4. TABLA EMBARCACIONES (ACTUALIZADA)
class Embarcacion(Base):
    __tablename__ = "embarcaciones"

    id_embarcacion = Column(String(50), primary_key=True, index=True) # ID único (ej: U-1-001)
    nombre = Column(String(100))
    capacidad_bodega = Column(Float, nullable=False)
    velocidad_promedio = Column(Float)
    consumo_combustible = Column(Float)
    
    # Nuevos campos del dataset
    material_casco = Column(String(50), default="ACERO NAVAL")
    tripulacion_maxima = Column(Integer, default=10)
    anio_fabricacion = Column(Integer, default=2020)
    
    estado = Column(String(50), default='EN_PUERTO')
    
    # Relaciones
    puerto_base_id = Column(String(20), ForeignKey("puertos.id_puerto"), nullable=True)
    puerto_base = relationship("Puerto", back_populates="embarcaciones")
    
    # Dueño del barco (Usuario)
    owner_id = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
    owner = relationship("Usuario", back_populates="mis_embarcaciones")

# 5. TABLA RESULTADOS (Opcional, para historial)
class HistorialRuta(Base):
    __tablename__ = "historial_rutas"
    id_ruta = Column(Integer, primary_key=True, index=True)
    id_embarcacion = Column(String(50), ForeignKey("embarcaciones.id_embarcacion"))
    fecha_calculo = Column(DateTime(timezone=True), server_default=func.now())
    distancia_total_km = Column(Float)
    
    embarcacion = relationship("Embarcacion")