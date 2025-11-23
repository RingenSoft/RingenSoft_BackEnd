from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# --- CONFIGURACIÓN DE CONEXIÓN ---
# Formato: mysql+pymysql://usuario:password@host:puerto/nombre_db
# AJUSTA ESTO CON TUS DATOS REALES DE MYSQL
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://root:root@localhost:3306/ringensoft_db"

# Crear el motor de conexión
engine = create_engine(
    SQLALCHEMY_DATABASE_URL
)

# Crear la sesión local para las consultas
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos ORM
Base = declarative_base()

# Dependencia para obtener la DB en cada petición
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()