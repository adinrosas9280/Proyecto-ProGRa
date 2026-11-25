"""
config.py
---------
Lee la configuración (host, puerto y API Key) desde un archivo externo ".env".

¿Por qué así?
La actividad pide que la API Key NUNCA esté escrita en el código fuente.
Con python-dotenv, las variables del archivo .env se cargan como variables de
entorno y las leemos con os.getenv(). El .env no se entrega con el proyecto.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Buscamos el .env junto a este archivo (sin importar desde dónde se ejecute).
load_dotenv(Path(__file__).with_name(".env"))

SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
# Modo del servidor: "multihilo" (por defecto) o "bloqueante" (solo para comparar)
MODO_SERVIDOR = os.getenv("MODO_SERVIDOR", "multihilo").lower()
# Segundos de espera artificial por solicitud (0 = sin retardo). Solo para pruebas.
RETARDO_SIMULADO = float(os.getenv("RETARDO_SIMULADO", "0"))

# ----------------------------------------------------------------------
# Suma de matrices (valores compartidos por cliente y servidor)
# ----------------------------------------------------------------------
# Dimensión máxima por lado. Si tu GPU/RAM no aguanta 4096, bájala aquí.
MAX_DIM_MATRIZ = 4096
# Hasta este tamaño el usuario puede capturar los valores a mano.
MAX_DIM_MANUAL = 10
# Hasta este tamaño el resultado se devuelve numérico; arriba, como imagen.
MAX_DIM_NUMERICO = 20
# Hilos del ThreadPoolExecutor del servidor.
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
