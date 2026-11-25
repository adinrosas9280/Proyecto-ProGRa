"""
interfaz.py  —  CAPA DE PRESENTACIÓN
------------------------------------
Ventana PySide6 con 3 gráficas (QtCharts):
    * Barras      -> comparar calificaciones entre películas (categorías)
    * Pastel      -> proporción de películas por género (partes de un total)
    * Dispersión  -> relación entre popularidad y calificación (2 variables numéricas)

Pestaña extra: "Suma de matrices" (ver pestana_matrices.py).

La interfaz solo habla con ClientePeliculas; nunca con el servidor ni con TMDB.
"""
import sys

from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
                              QPieSeries, QScatterSeries, QValueAxis)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from cliente import ClientePeliculas
from pestana_matrices import PestanaMatrices


class HiloConsulta(QThread):
    """
    Ejecuta la consulta al servidor en un hilo aparte.
    Si lo hiciéramos en el hilo principal, la ventana se congelaría mientras
    se espera la respuesta de la red.
    Los hilos se comunican con la interfaz mediante SEÑALES (signals).
    """
    resultado = Signal(dict)  # se emite cuando llegan los datos
    fallo = Signal(str)       # se emite si algo sale mal

    def __init__(self, cliente, paginas):
        super().__init__()
        self.cliente = cliente
        self.paginas = paginas

    def run(self):
        try:
            self.resultado.emit(self.cliente.obtener_peliculas_populares(self.paginas))
        except Exception as error:
            self.fallo.emit(str(error))


# ----------------------------------------------------------------------
# Funciones que construyen cada gráfica (reciben datos, devuelven QChartView)
# ----------------------------------------------------------------------
def _vista(grafica):
    """Envuelve un QChart en un QChartView (el widget que se muestra en pantalla)."""
    vista = QChartView(grafica)
    vista.setRenderHint(QPainter.Antialiasing)  # bordes suaves
    return vista


def crear_grafica_barras(top):
    """Barras: Top 10 películas por calificación."""
    conjunto = QBarSet("Calificación (0-10)")
    nombres = []
    for p in top:
        conjunto.append(p["calificacion"])
        # recortamos títulos largos para que quepan en el eje
        nombres.append(p["titulo"] if len(p["titulo"]) <= 18 else p["titulo"][:17] + "…")

    serie = QBarSeries()
    serie.append(conjunto)
    serie.setLabelsVisible(True)  # muestra el valor encima de cada barra

    grafica = QChart()
    grafica.addSeries(serie)
    grafica.setTitle("Top 10 películas populares mejor calificadas")

    # Eje X categórico (nombres) y eje Y numérico (calificación)
    eje_x = QBarCategoryAxis()
    eje_x.append(nombres)
    eje_x.setLabelsAngle(-45)
    eje_y = QValueAxis()
    eje_y.setRange(0, 10)
    eje_y.setTitleText("Calificación promedio")

    grafica.addAxis(eje_x, Qt.AlignmentFlag.AlignBottom)
    grafica.addAxis(eje_y, Qt.AlignmentFlag.AlignLeft)
    serie.attachAxis(eje_x)  # IMPORTANTE: enlazar la serie con sus ejes
    serie.attachAxis(eje_y)
    grafica.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    return _vista(grafica)


def crear_grafica_pastel(generos):
    """Pastel: reparto de películas por género."""
    serie = QPieSeries()
    for g in generos:
        serie.append(g["genero"], g["cantidad"])

    # Mostramos nombre y porcentaje en cada rebanada
    for rebanada in serie.slices():
        rebanada.setLabelVisible(True)
        rebanada.setLabel(f"{rebanada.label()} {rebanada.percentage() * 100:.1f}%")

    grafica = QChart()
    grafica.addSeries(serie)
    grafica.setTitle("Distribución de películas por género")
    grafica.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
    return _vista(grafica)


def crear_grafica_dispersion(puntos):
    """Dispersión: ¿las películas más populares son mejor calificadas?"""
    serie = QScatterSeries()
    serie.setName("Película")
    serie.setMarkerSize(10.0)
    for p in puntos:
        serie.append(p["popularidad"], p["calificacion"])

    grafica = QChart()
    grafica.addSeries(serie)
    grafica.setTitle("Popularidad vs. calificación")

    eje_x = QValueAxis()
    eje_x.setTitleText("Popularidad (índice TMDB)")
    eje_x.setRange(0, max((p["popularidad"] for p in puntos), default=100) * 1.05)
    eje_y = QValueAxis()
    eje_y.setTitleText("Calificación (0-10)")
    eje_y.setRange(0, 10)

    grafica.addAxis(eje_x, Qt.AlignmentFlag.AlignBottom)
    grafica.addAxis(eje_y, Qt.AlignmentFlag.AlignLeft)
    serie.attachAxis(eje_x)
    serie.attachAxis(eje_y)
    return _vista(grafica)


# ----------------------------------------------------------------------
# Ventana principal
# ----------------------------------------------------------------------
class VentanaPeliculas(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cliente: películas (TMDB) y suma de matrices (PyTorch)")
        self.resize(1050, 720)
        self.cliente = ClientePeliculas()
        self.hilo = None

        # --- Barra superior: páginas a consultar + botón + estado ---
        self.spin_paginas = QSpinBox()
        self.spin_paginas.setRange(1, 5)
        self.spin_paginas.setValue(3)
        self.boton = QPushButton("Cargar datos")
        self.boton.clicked.connect(self.cargar_datos)
        self.estado = QLabel("Pulsa «Cargar datos» para comenzar.")

        barra = QHBoxLayout()
        barra.addWidget(QLabel("Páginas (20 películas c/u):"))
        barra.addWidget(self.spin_paginas)
        barra.addWidget(self.boton)
        barra.addWidget(self.estado, stretch=1)

        # --- Sub-pestañas: una por gráfica (se reconstruyen con pestanas.clear()) ---
        self.pestanas = QTabWidget()

        # --- Página "Películas": todo lo que ya existía ---
        pagina_peliculas = QWidget()
        layout = QVBoxLayout(pagina_peliculas)
        layout.addLayout(barra)
        layout.addWidget(self.pestanas)

        # --- Pestañas principales ---
        # Antes la ventana mostraba directamente la página de películas. Ahora hay un
        # QTabWidget superior; así pestanas.clear() solo borra las gráficas, no la pestaña nueva.
        self.principal = QTabWidget()
        self.principal.addTab(pagina_peliculas, "Películas")
        self.principal.addTab(PestanaMatrices(self.cliente), "Suma de matrices")
        self.setCentralWidget(self.principal)

    def cargar_datos(self):
        self.boton.setEnabled(False)  # evita clics repetidos mientras carga
        self.estado.setText("Consultando al servidor…")
        self.hilo = HiloConsulta(self.cliente, self.spin_paginas.value())
        self.hilo.resultado.connect(self.mostrar_graficas)
        self.hilo.fallo.connect(self.mostrar_error)
        self.hilo.start()

    def mostrar_graficas(self, datos):
        """Se ejecuta cuando llegan los datos: reconstruye las pestañas."""
        self.pestanas.clear()
        self.pestanas.addTab(crear_grafica_barras(datos["top_calificadas"]), "Barras")
        self.pestanas.addTab(crear_grafica_pastel(datos["generos"]), "Pastel")
        self.pestanas.addTab(crear_grafica_dispersion(datos["dispersion"]), "Dispersión")
        self.estado.setText(f"Listo: {datos['total']} películas analizadas.")
        self.boton.setEnabled(True)

    def mostrar_error(self, mensaje):
        self.estado.setText(f"Error: {mensaje}")
        self.boton.setEnabled(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = VentanaPeliculas()
    ventana.show()
    sys.exit(app.exec())
