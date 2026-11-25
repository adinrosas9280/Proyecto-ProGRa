"""
servidor.py  —  CAPA DE LÓGICA / COMUNICACIÓN (lado servidor)
-------------------------------------------------------------
Recibe solicitudes JSON de los clientes, decide qué hacer según el campo
"tipo" y responde.

ACTIVIDAD: SUMA DE MATRICES CON PYTORCH (CUDA / MPS / CPU)
----------------------------------------------------------
Se añadió el tipo de solicitud "SUMA_MATRICES". La matemática vive en
procesador_matrices.py; este archivo solo valida, delega y responde.
La concurrencia ahora la administra un ThreadPoolExecutor.

Este servidor puede funcionar en dos modos (variable MODO_SERVIDOR del .env):

  * "multihilo"  (por defecto): el hilo principal SOLO acepta conexiones y
                 envía cada cliente a un POOL de hilos (ThreadPoolExecutor).
  * "bloqueante": el hilo principal acepta Y atiende al cliente. Mientras lo
                 atiende NO puede volver a accept(), así que los demás
                 clientes esperan en cola. Existe solo para COMPARAR
                 el comportamiento en el documento técnico.

Formato de solicitud : {"tipo": "peliculas_populares", "paginas": 3}
                       {"tipo": "SUMA_MATRICES", "modo": "valores"|"generar",
                        "filas": 3, "columnas": 3,
                        "matriz_a": [[...]], "matriz_b": [[...]]}   (solo modo "valores")
Formato de respuesta : {"estado": "ok", "datos": {...}}
                    o  {"estado": "error", "mensaje": "..."}
"""
import math
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

from config import (MAX_DIM_MANUAL, MAX_DIM_MATRIZ, MAX_DIM_NUMERICO, MAX_WORKERS,
                    MODO_SERVIDOR, RETARDO_SIMULADO, SERVER_HOST, SERVER_PORT, TMDB_API_KEY)
from procesador_matrices import ProcesadorMatrices, matriz_a_png_base64
from protocolo import enviar_json, recibir_json
from tmdb_api import TMDBApi

# Una sola instancia compartida por todos los hilos.
# Es segura de compartir: TMDBApi solo guarda api_key e idioma (nunca se
# modifican) y cada petición usa requests.get(), que no comparte estado.
# Si no hay API key (por ejemplo en Colab, donde solo probamos matrices),
# el servidor arranca igual y solo rechaza las solicitudes de películas.
try:
    api = TMDBApi(TMDB_API_KEY)
except ValueError:
    api = None

# Una sola instancia del procesador de matrices: elige el dispositivo
# (CUDA -> MPS -> CPU) UNA vez al arrancar y la usan todos los hilos.
# Tiene su propio candado interno para medir tiempos sin interferencias.
procesador = ProcesadorMatrices()

# ----------------------------------------------------------------------
# UTILIDADES PARA OBSERVAR LA CONCURRENCIA
# ----------------------------------------------------------------------
# print() desde varios hilos a la vez puede mezclar los mensajes en una
# misma línea. Un Lock garantiza que cada mensaje se imprima completo.
_candado_consola = threading.Lock()


def log(mensaje):
    """Imprime con hora y nombre del hilo que lo emite."""
    hora = time.strftime("%H:%M:%S")
    nombre_hilo = threading.current_thread().name
    with _candado_consola:
        print(f"[{hora}] [{nombre_hilo:<12}] {mensaje}", flush=True)


# ----------------------------------------------------------------------
# LÓGICA DE NEGOCIO (sin cambios respecto a la Unidad 1)
# ----------------------------------------------------------------------
def construir_datos_graficas(peliculas):
    """Convierte la lista de películas en tres estructuras listas para graficar."""

    # --- 1) Barras: Top 10 por calificación (solo con suficientes votos,
    #        para que una película con 3 votos de "10" no gane). ---
    confiables = [p for p in peliculas if p["votos"] >= 100]
    top10 = sorted(confiables, key=lambda p: p["calificacion"], reverse=True)[:10]

    # --- 2) Pastel: cuántas películas hay por género. ---
    conteo = Counter(g for p in peliculas for g in p["generos"])
    principales = conteo.most_common(7)
    otros = sum(conteo.values()) - sum(c for _, c in principales)
    generos = [{"genero": g, "cantidad": c} for g, c in principales]
    if otros > 0:
        generos.append({"genero": "Otros", "cantidad": otros})

    # --- 3) Dispersión: popularidad (x) vs calificación (y). ---
    dispersion = [
        {"titulo": p["titulo"], "popularidad": p["popularidad"], "calificacion": p["calificacion"]}
        for p in peliculas if p["votos"] >= 100
    ]

    return {
        "total": len(peliculas),
        "top_calificadas": [{"titulo": p["titulo"], "calificacion": p["calificacion"]} for p in top10],
        "generos": generos,
        "dispersion": dispersion,
    }


# ----------------------------------------------------------------------
# SUMA DE MATRICES: validación + armado de la respuesta
# ----------------------------------------------------------------------
def _dimension_valida(valor, nombre):
    """Entero entre 1 y MAX_DIM_MATRIZ. (bool es subclase de int: lo rechazamos.)"""
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise ValueError(f"'{nombre}' debe ser un número entero")
    if not 1 <= valor <= MAX_DIM_MATRIZ:
        raise ValueError(f"'{nombre}' debe estar entre 1 y {MAX_DIM_MATRIZ}")
    return valor


def _matriz_valida(matriz, filas, columnas, nombre):
    """Comprueba que sea una lista de `filas` listas de `columnas` números finitos."""
    if not isinstance(matriz, list) or len(matriz) != filas:
        raise ValueError(f"La matriz {nombre} debe tener {filas} filas")
    for i, fila in enumerate(matriz):
        if not isinstance(fila, list) or len(fila) != columnas:
            raise ValueError(f"La fila {i + 1} de la matriz {nombre} debe tener {columnas} columnas")
        for valor in fila:
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
                raise ValueError(f"La matriz {nombre} contiene un valor no numérico o infinito")
    return matriz


def manejar_suma_matrices(solicitud):
    """
    Valida la solicitud, obtiene A y B (del cliente o generadas aquí), suma con
    PyTorch y arma los `datos` de la respuesta. Lanza ValueError si algo es inválido.
    """
    filas = _dimension_valida(solicitud.get("filas"), "filas")
    columnas = _dimension_valida(solicitud.get("columnas"), "columnas")
    modo = solicitud.get("modo")

    if modo == "valores":
        # El cliente manda los valores: solo se permite para matrices pequeñas.
        if filas > MAX_DIM_MANUAL or columnas > MAX_DIM_MANUAL:
            raise ValueError(f"Con valores del cliente el máximo es {MAX_DIM_MANUAL}x{MAX_DIM_MANUAL}")
        a = _matriz_valida(solicitud.get("matriz_a"), filas, columnas, "A")
        b = _matriz_valida(solicitud.get("matriz_b"), filas, columnas, "B")
    elif modo == "generar":
        # El servidor genera las matrices aleatorias directo en el dispositivo.
        a, b = procesador.generar_aleatorias(filas, columnas)
    else:
        raise ValueError("'modo' debe ser 'valores' o 'generar'")

    r = procesador.sumar(a, b)
    log(f"  Suma {filas}x{columnas} en {r.dispositivo} ({r.detalle}): "
        f"{r.tiempo_suma_ms:.3f} ms (total {r.tiempo_total_ms:.1f} ms)")

    datos = {
        "mensaje": "Suma realizada correctamente",
        "filas": filas,
        "columnas": columnas,
        "dispositivo": r.dispositivo,
        "detalle": r.detalle,
        "tiempo_suma_ms": round(r.tiempo_suma_ms, 3),
        "tiempo_total_ms": round(r.tiempo_total_ms, 3),
    }

    if filas <= MAX_DIM_NUMERICO and columnas <= MAX_DIM_NUMERICO:
        # Matriz pequeña: se devuelve como lista de listas.
        datos["formato"] = "numerico"
        datos["resultado"] = r.resultado.tolist()
    else:
        # Matriz grande: se devuelve una imagen en escala de grises.
        png_b64, minimo, maximo, ancho, alto = matriz_a_png_base64(r.resultado)
        datos.update({
            "formato": "imagen",
            "imagen_png_b64": png_b64,
            "imagen_ancho": ancho,
            "imagen_alto": alto,
            "valor_minimo": minimo,
            "valor_maximo": maximo,
        })
    return datos


def manejar_solicitud(solicitud):
    """Despachador: elige qué hacer según el tipo de solicitud."""
    tipo = solicitud.get("tipo")

    if tipo == "peliculas_populares":
        if api is None:
            return {"estado": "error", "mensaje": "El servidor no tiene TMDB_API_KEY configurada"}
        # Limitamos las páginas (1-5) para no abusar de la API.
        paginas = max(1, min(int(solicitud.get("paginas", 3)), 5))
        try:
            peliculas = api.obtener_peliculas_populares(paginas)
            return {"estado": "ok", "datos": construir_datos_graficas(peliculas)}
        except requests.RequestException as error:
            return {"estado": "error", "mensaje": f"Error al consultar TMDB: {error}"}

    if tipo == "SUMA_MATRICES":
        try:
            return {"estado": "ok", "datos": manejar_suma_matrices(solicitud)}
        except ValueError as error:                    # solicitud inválida
            return {"estado": "error", "mensaje": f"Solicitud inválida: {error}"}
        except (RuntimeError, MemoryError) as error:   # incluye "CUDA out of memory"
            procesador.liberar_memoria()
            return {"estado": "error", "mensaje": f"No se pudo procesar la suma: {error}"}

    return {"estado": "error", "mensaje": f"Tipo de solicitud desconocido: {tipo}"}


# ----------------------------------------------------------------------
# ATENCIÓN AL CLIENTE
# ----------------------------------------------------------------------
def atender_cliente(conexion, direccion):
    """
    Atiende a UN cliente. En modo multihilo se ejecuta dentro de un hilo
    del pool (Worker_N); en modo bloqueante, dentro del hilo principal.

    Cada ejecución trabaja con SU conexión y es responsable de cerrarla.
    """
    log(f"Atendiendo al cliente {direccion}")
    try:
        solicitud = recibir_json(conexion)
        log(f"Solicitud recibida: {str(solicitud)[:200]}")  # recortada: puede traer matrices

        # --- Inicio del procesamiento ---
        inicio = time.perf_counter()
        log("▶ INICIA procesamiento")

        # Retardo artificial (solo para pruebas). time.sleep() NO bloquea a
        # los demás hilos: solo "duerme" a este.
        if RETARDO_SIMULADO > 0:
            log(f"  (retardo simulado de {RETARDO_SIMULADO}s)")
            time.sleep(RETARDO_SIMULADO)

        respuesta = manejar_solicitud(solicitud)
        enviar_json(conexion, respuesta)

        # --- Fin del procesamiento ---
        duracion = time.perf_counter() - inicio
        log(f"■ TERMINA procesamiento ({duracion:.2f}s) -> estado: {respuesta['estado']}")

    except Exception as error:  # nunca dejamos que un cliente tumbe el servidor
        log(f"[!] Error con {direccion}: {error}")
    finally:
        conexion.close()  # cada atención cierra SU conexión
        log(f"Conexión cerrada con {direccion}")


# ----------------------------------------------------------------------
# ACEPTACIÓN DE CONEXIONES (hilo principal)
# ----------------------------------------------------------------------
def main():
    # El hilo principal se llama "MainThread" por defecto; lo renombramos
    # para que en los logs quede claro quién acepta y quién atiende.
    threading.current_thread().name = "Principal"

    # ThreadPoolExecutor: crea MAX_WORKERS hilos UNA vez y los reutiliza.
    # Si llegan más clientes que hilos, las tareas esperan en una cola interna
    # (en vez de crear hilos sin límite). Los hilos se llaman Worker_0, Worker_1...
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Worker")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servidor.bind((SERVER_HOST, SERVER_PORT))
        servidor.listen()
        log(f"Servidor escuchando en {SERVER_HOST}:{SERVER_PORT} | modo: {MODO_SERVIDOR} | hilos: {MAX_WORKERS}")
        log(f"Dispositivo para matrices: {procesador.nombre} ({procesador.detalle})")

        try:
            while True:
                # accept() bloquea SOLO hasta que llega un cliente nuevo.
                conexion, direccion = servidor.accept()
                log(f"Conexión ACEPTADA de {direccion}")

                if MODO_SERVIDOR == "bloqueante":
                    # --- MODELO ORIGINAL: se atiende aquí mismo. ---
                    # Hasta que atender_cliente() termine, no se vuelve a accept().
                    atender_cliente(conexion, direccion)
                else:
                    # --- MODELO CON POOL: entregamos la tarea y regresamos de inmediato. ---
                    # submit() NO espera a que termine (no es como hilo.join()).
                    pool.submit(atender_cliente, conexion, direccion)
                    log(f"Tarea enviada al pool para {direccion}; vuelvo a escuchar")
        except KeyboardInterrupt:
            log("Servidor detenido por el usuario")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
