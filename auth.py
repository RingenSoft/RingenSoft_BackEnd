from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from typing import Optional

# --- CONFIGURACIÓN DE SEGURIDAD ---
SECRET_KEY = "ringensoft_secret_key_super_segura" # En prod esto va en variables de entorno
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 horas de sesión

# Contexto para encriptar contraseñas (Hashing)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 1. Funciones de Contraseña
def verificar_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def encriptar_password(password):
    return pwd_context.hash(password)

# 2. Crear Token de Acceso (JWT)
def crear_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# 3. Decodificar Token (Verificar identidad)
def decodificar_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None