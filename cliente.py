"""
cliente.py  —  CAPA DE LÓGICA / COMUNICACIÓN (lado cliente)
-----------------------------------------------------------
Se conecta al servidor, envía una solicitud y devuelve la respuesta ya
convertida en diccionario de Python. NO conoce TMDB, PyTorch ni PySide6.

IMPORTANTE: aquí NO se suma ninguna matriz. El cálculo con PyTorch ocurre
en el servidor; el cliente solo arma la solicitud y entiende la respuesta.
"""
import base64
import socket

from config import SERVER_HOST, SERVER_PORT
from protocolo import enviar_json, recibir_json


class ClientePeliculas:
    def __init__(self, host=SERVER_HOST, puerto=SERVER_PORT):
        self.host = host
        self.puerto = puerto

    def _solicitar(self, solicitud, timeout=30):
        """Abre conexión, envía la solicitud, espera la respuesta completa."""
        with socket.create_connection((self.host, self.puerto), timeout=timeout) as sock:
            enviar_json(sock, solicitud)
            return recibir_json(sock)

    def obtener_peliculas_populares(self, paginas=3):
        """Pide los datos para las gráficas. Lanza RuntimeError si el servidor reporta error."""
        respuesta = self._solicitar({"tipo": "peliculas_populares", "paginas": paginas})
        if respuesta.get("estado") != "ok":
            raise RuntimeError(respuesta.get("mensaje", "Error desconocido del servidor"))
        return respuesta["datos"]

    def sumar_matrices(self, filas, columnas, modo, matriz_a=None, matriz_b=None):
        """
        Pide al servidor la suma de dos matrices (SUMA_MATRICES).

        modo = "valores" -> se envían matriz_a y matriz_b (listas de listas de float)
        modo = "generar" -> solo se envían las dimensiones; el servidor genera las matrices

        Devuelve un diccionario con: filas, columnas, dispositivo, detalle,
        tiempo_suma_ms, tiempo_total_ms y "formato":
            "numerico" -> trae "resultado" (lista de listas)
            "imagen"   -> trae "imagen_png" (bytes PNG ya decodificados),
                          además de valor_minimo / valor_maximo / imagen_ancho / imagen_alto
        Lanza RuntimeError si el servidor responde con error.
        """
        solicitud = {"tipo": "SUMA_MATRICES", "modo": modo, "filas": filas, "columnas": columnas}
        if modo == "valores":
            solicitud["matriz_a"] = matriz_a
            solicitud["matriz_b"] = matriz_b

        # timeout largo: por el túnel y el arranque de la GPU la primera vez puede tardar
        respuesta = self._solicitar(solicitud, timeout=120)
        if respuesta.get("estado") != "ok":
            raise RuntimeError(respuesta.get("mensaje", "Error desconocido del servidor"))

        datos = respuesta["datos"]
        if datos.get("formato") == "imagen":
            # El PNG viaja dentro del JSON en base64 (texto); aquí lo volvemos bytes.
            datos["imagen_png"] = base64.b64decode(datos.pop("imagen_png_b64"))
        return datos
