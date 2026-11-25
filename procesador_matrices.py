"""
procesador_matrices.py  —  CAPA DE PROCESAMIENTO (matemáticas con PyTorch)
--------------------------------------------------------------------------
Clase independiente que suma matrices usando PyTorch en el mejor dispositivo
disponible. NO sabe nada de sockets, JSON ni PySide6: recibe matrices y
devuelve resultados. Por eso se puede reutilizar para otras operaciones
(resta, multiplicación, etc.) sin tocar el servidor.

CONCEPTOS CLAVE
---------------
* Tensor: arreglo n-dimensional de números. Una matriz es un tensor de 2
  dimensiones (filas x columnas). Un escalar es de 0 dim, un vector de 1 dim.
* Dispositivo: dónde "viven" los tensores y dónde se calcula.
      CUDA -> GPU NVIDIA        MPS -> GPU de Apple (Metal)        CPU -> procesador
  Orden de preferencia:  CUDA -> MPS -> CPU
* C = A + B se hace con el operador "+" de PyTorch (operación VECTORIZADA):
  en una GPU, miles de hilos suman elementos distintos a la vez. Nunca usamos
  ciclos for anidados de Python.
"""
import base64
import io
import platform
import threading
import time
from dataclasses import dataclass

import numpy as np
import torch
from PIL import Image


# ----------------------------------------------------------------------
# Selección del dispositivo
# ----------------------------------------------------------------------
def seleccionar_dispositivo(forzar=None):
    """
    Devuelve el torch.device a usar, siguiendo CUDA -> MPS -> CPU.
    `forzar` ("cpu", por ejemplo) sirve para comparar tiempos en el benchmark.
    """
    if forzar is not None:
        return torch.device(forzar)
    if torch.cuda.is_available():            # ¿hay GPU NVIDIA con CUDA?
        return torch.device("cuda")
    if torch.backends.mps.is_available():    # ¿hay GPU Apple con Metal?
        return torch.device("mps")
    return torch.device("cpu")               # alternativa universal


def describir_dispositivo(dispositivo):
    """Nombre legible del hardware (ej. 'Tesla T4')."""
    if dispositivo.type == "cuda":
        return torch.cuda.get_device_name(0)
    if dispositivo.type == "mps":
        return "GPU Apple (Metal)"
    return platform.processor() or platform.machine() or "CPU"


# ----------------------------------------------------------------------
# Resultado de una suma (un contenedor simple de datos)
# ----------------------------------------------------------------------
@dataclass
class ResultadoSuma:
    resultado: np.ndarray     # matriz C ya en CPU (numpy), float32
    filas: int
    columnas: int
    dispositivo: str          # "CUDA", "MPS" o "CPU"
    detalle: str              # nombre del hardware
    tiempo_suma_ms: float     # SOLO la operación C = A + B
    tiempo_total_ms: float    # crear tensores + transferencias + suma + traer resultado


# ----------------------------------------------------------------------
# Clase principal
# ----------------------------------------------------------------------
class ProcesadorMatrices:
    def __init__(self, forzar_dispositivo=None):
        self.dispositivo = seleccionar_dispositivo(forzar_dispositivo)
        self.nombre = self.dispositivo.type.upper()          # "CUDA" / "MPS" / "CPU"
        self.detalle = describir_dispositivo(self.dispositivo)

        # Varios hilos del servidor pueden pedir sumas a la vez. La GPU ejecuta
        # el trabajo en una cola, así que si dos hilos midieran tiempos al
        # mismo tiempo, cada uno mediría también el trabajo del otro. Este
        # candado hace que cada suma se mida "limpia" (una a la vez).
        self._candado = threading.Lock()

        self._calentar()

    # ------------------------------------------------------------------
    def _calentar(self):
        """
        La PRIMERA operación en CUDA es lenta (inicializa el contexto, carga
        kernels). Hacemos una suma diminuta al arrancar para que ese costo no
        contamine la primera medición real.
        """
        a = torch.ones(8, 8, dtype=torch.float32, device=self.dispositivo)
        _ = a + a
        self._sincronizar()

    def _sincronizar(self):
        """
        Las GPU trabajan de forma ASÍNCRONA: Python lanza la operación y sigue
        de largo. Sin synchronize() mediríamos solo el tiempo de "lanzar" la
        suma, no de ejecutarla.
        """
        if self.dispositivo.type == "cuda":
            torch.cuda.synchronize()
        elif self.dispositivo.type == "mps":
            torch.mps.synchronize()

    def liberar_memoria(self):
        """Útil después de un error de memoria (OOM) en la GPU."""
        if self.dispositivo.type == "cuda":
            torch.cuda.empty_cache()
        elif self.dispositivo.type == "mps":
            torch.mps.empty_cache()

    # ------------------------------------------------------------------
    def _a_tensor(self, matriz):
        """Convierte lista de listas / numpy / tensor a un tensor float32 EN el dispositivo."""
        if isinstance(matriz, torch.Tensor):
            tensor = matriz.to(device=self.dispositivo, dtype=torch.float32)
        else:
            # torch.tensor copia los datos y los coloca directo en el dispositivo
            tensor = torch.tensor(matriz, dtype=torch.float32, device=self.dispositivo)
        if tensor.ndim != 2:
            raise ValueError("Cada matriz debe tener exactamente 2 dimensiones")
        return tensor

    def generar_aleatorias(self, filas, columnas):
        """
        Crea dos matrices aleatorias float32 DIRECTAMENTE en el dispositivo
        (evita construir 16 millones de números en Python y copiarlos a la GPU).
        Valores en [0, 1).
        """
        a = torch.rand(filas, columnas, dtype=torch.float32, device=self.dispositivo)
        b = torch.rand(filas, columnas, dtype=torch.float32, device=self.dispositivo)
        return a, b

    # ------------------------------------------------------------------
    def sumar(self, a, b):
        """
        C = A + B, donde C[i][j] = A[i][j] + B[i][j].
        `a` y `b` pueden ser listas, arreglos numpy o tensores.
        """
        with self._candado:
            inicio_total = time.perf_counter()

            # 1) Crear tensores y moverlos al dispositivo
            ta = self._a_tensor(a)
            tb = self._a_tensor(b)
            if ta.shape != tb.shape:
                raise ValueError(f"Las matrices deben tener las mismas dimensiones: {tuple(ta.shape)} vs {tuple(tb.shape)}")
            self._sincronizar()

            # 2) La suma en sí (lo único que medimos como "tiempo de suma")
            inicio_suma = time.perf_counter()
            tc = ta + tb
            self._sincronizar()
            fin_suma = time.perf_counter()

            # 3) Traer el resultado a la CPU (numpy) para poder enviarlo por red
            resultado = tc.cpu().numpy()
            fin_total = time.perf_counter()

        return ResultadoSuma(
            resultado=resultado,
            filas=resultado.shape[0],
            columnas=resultado.shape[1],
            dispositivo=self.nombre,
            detalle=self.detalle,
            tiempo_suma_ms=(fin_suma - inicio_suma) * 1000,
            tiempo_total_ms=(fin_total - inicio_total) * 1000,
        )


# ----------------------------------------------------------------------
# Representación visual (responsabilidad aparte: no es matemática)
# ----------------------------------------------------------------------
def matriz_a_png_base64(matriz, lado_maximo=512):
    """
    Convierte una matriz en una imagen PNG en escala de grises.

    ¿Por qué normalizar? Un píxel solo admite enteros de 0 a 255, pero la
    matriz puede tener cualquier rango (por ejemplo 0.3 a 1.9, o -500 a 900).
    Min-max lleva el mínimo a 0 (negro) y el máximo a 255 (blanco):
            pixel = (valor - mínimo) / (máximo - mínimo) * 255

    Si la matriz es muy grande (ej. 4096x4096) se reduce promediando bloques
    (resampling BOX) para que la imagen viaje rápido por la red.

    Devuelve (png_en_base64, mínimo, máximo, ancho_img, alto_img).
    """
    m = np.asarray(matriz, dtype=np.float32)
    minimo, maximo = float(m.min()), float(m.max())
    rango = maximo - minimo

    if rango == 0:                      # matriz constante: evitamos dividir entre 0
        normalizada = np.zeros_like(m)
    else:
        normalizada = (m - minimo) / rango

    pixeles = (normalizada * 255).astype(np.uint8)
    imagen = Image.fromarray(pixeles)   # uint8 2D -> escala de grises ("L")

    if max(imagen.size) > lado_maximo:
        imagen.thumbnail((lado_maximo, lado_maximo), Image.Resampling.BOX)

    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    png_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return png_b64, minimo, maximo, imagen.size[0], imagen.size[1]
