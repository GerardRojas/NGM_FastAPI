# utils/auth.py

from passlib.context import CryptContext

# Configuración de passlib con bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """
    Recibe una contraseña en texto plano y regresa el hash seguro.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compara una contraseña en texto plano contra el hash guardado.
    Regresa True si coinciden, False si no.
    """
    return pwd_context.verify(plain_password, hashed_password)
