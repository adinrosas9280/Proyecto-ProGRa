"""
protocolo.py
------------
Protocolo de comunicación compartido por el cliente y el servidor.

Problema: TCP es un flujo de bytes, no de "mensajes". Una respuesta grande
puede llegar partida en varios recv(). Solución clásica: ANTES de cada mensaje
se envían 4 bytes que indican cuántos bytes mide el mensaje (prefijo de longitud).

Formato de cada mensaje:
    [4 bytes: longitud (big-endian)] [N bytes: JSON en UTF-8]

NOTA: si tu servidor de actividades anteriores ya tiene su propio protocolo,
usa ese en lugar de este archivo. Lo importante es que cliente y servidor
usen el mismo.
"""
import json
import struct


def enviar_json(sock, objeto):
    """Convierte un diccionario a JSON y lo envía con su prefijo de longitud."""
    datos = json.dumps(objeto).encode("utf-8")
    sock.sendall(struct.pack(">I", len(datos)) + datos)


def _recibir_exacto(sock, cantidad):
    """Lee EXACTAMENTE `cantidad` bytes (repite recv hasta completar)."""
    buffer = b""
    while len(buffer) < cantidad:
        trozo = sock.recv(cantidad - len(buffer))
        if not trozo:  # el otro extremo cerró la conexión
            raise ConnectionError("Conexión cerrada antes de recibir todo el mensaje")
        buffer += trozo
    return buffer


def recibir_json(sock):
    """Lee un mensaje completo y lo devuelve como diccionario de Python."""
    cabecera = _recibir_exacto(sock, 4)
    (longitud,) = struct.unpack(">I", cabecera)
    return json.loads(_recibir_exacto(sock, longitud).decode("utf-8"))
