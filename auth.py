import os
import logging
import bcrypt
from jose import jwt, JWTError
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN DE SEGURIDAD ---
_DEFAULT_KEY = "dev-only-secret-change-in-production-min-32-chars"
SECRET_KEY = os.getenv("SECRET_KEY", _DEFAULT_KEY)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 horas de sesión

if SECRET_KEY == _DEFAULT_KEY:
    logger.warning(
        "SECRET_KEY no está configurada. Usando clave de desarrollo. "
        "Configura la variable de entorno SECRET_KEY antes de desplegar en producción."
    )

# 1. Funciones de Contraseña
def verificar_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def encriptar_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

# 2. Crear Token de Acceso (JWT)
def crear_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# 3. Decodificar Token (Verificar identidad)
def decodificar_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
