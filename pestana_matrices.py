"""
pestana_matrices.py  —  CAPA DE PRESENTACIÓN (pestaña "Suma de matrices")
-------------------------------------------------------------------------
Widget PySide6 que permite:
    * elegir filas/columnas (QSpinBox, de 1 a MAX_DIM_MATRIZ)
    * capturar A y B a mano (QTableWidget) solo si caben en MAX_DIM_MANUAL x MAX_DIM_MANUAL,
      o pedir que el servidor las genere
    * enviar la solicitud con ClientePeliculas.sumar_matrices() (sin bloquear la ventana)
    * ver el resultado: tabla numérica (<= 20x20) o imagen en escala de grises (> 20x20)
    * ver dimensiones, dispositivo del servidor y tiempos

Como el resto de la interfaz, solo habla con el cliente; nunca con el servidor ni con PyTorch.
"""
import math
import random

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config import MAX_DIM_MANUAL, MAX_DIM_MATRIZ


class HiloSuma(QThread):
    """Hace la consulta de suma en un hilo aparte para no congelar la ventana (igual que HiloConsulta)."""
    resultado = Signal(dict)
    fallo = Signal(str)

    def __init__(self, cliente, filas, columnas, modo, matriz_a, matriz_b):
        super().__init__()
        self.cliente = cliente
        self.argumentos = (filas, columnas, modo, matriz_a, matriz_b)

    def run(self):
        try:
            self.resultado.emit(self.cliente.sumar_matrices(*self.argumentos))
        except Exception as error:
            self.fallo.emit(str(error))


class EtiquetaImagen(QLabel):
    """
    QLabel que muestra un QPixmap ajustado al espacio disponible SIN deformarlo.
    Guarda la imagen original y la reescala cada vez que cambia el tamaño del widget.
    """

    def __init__(self):
        super().__init__()
        self._original = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # "Ignored": la etiqueta no exige el tamaño de la imagen; se adapta a lo que haya.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(60, 60)

    def poner_imagen(self, datos_png):
        pixmap = QPixmap()
        pixmap.loadFromData(datos_png, "PNG")
        self._original = pixmap
        self._ajustar()

    def _ajustar(self):
        if self._original is None or self._original.isNull():
            return
        destino = self.size()
        factor = min(destino.width() / self._original.width(), destino.height() / self._original.height())
        # Si hay que AGRANDAR una imagen pequeña, usamos el modo rápido (píxeles nítidos);
        # si hay que reducirla, el modo suave se ve mejor.
        modo = Qt.TransformationMode.FastTransformation if factor > 1 else Qt.TransformationMode.SmoothTransformation
        self.setPixmap(self._original.scaled(destino, Qt.AspectRatioMode.KeepAspectRatio, modo))

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._ajustar()


def _crear_tabla(editable):
    tabla = QTableWidget()
    tabla.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)  # desplazamiento horizontal
    tabla.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)    # y vertical
    if not editable:
        tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    return tabla


class PestanaMatrices(QWidget):
    def __init__(self, cliente):
        super().__init__()
        self.cliente = cliente
        self.hilo = None
        self._crear_widgets()
        self._armar_layout()
        self._conectar_senales()
        self._al_cambiar_dimensiones()  # estado inicial coherente

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _crear_widgets(self):
        # --- Dimensiones ---
        self.spin_filas = QSpinBox()
        self.spin_columnas = QSpinBox()
        for spin in (self.spin_filas, self.spin_columnas):
            spin.setRange(1, MAX_DIM_MATRIZ)
            spin.setValue(3)
            spin.setKeyboardTracking(False)  # no reaccionar a cada tecla (al teclear "1024" pasaría por 1, 10, 102...)

        # --- Origen de los valores ---
        self.radio_manual = QRadioButton(f"Capturar manualmente (hasta {MAX_DIM_MANUAL}×{MAX_DIM_MANUAL})")
        self.radio_servidor = QRadioButton("Generar aleatorias en el servidor")
        self.radio_manual.setChecked(True)
        self.grupo_origen = QButtonGroup(self)
        self.grupo_origen.addButton(self.radio_manual)
        self.grupo_origen.addButton(self.radio_servidor)

        # --- Botones ---
        self.boton_aleatorio = QPushButton("Llenar con valores aleatorios")
        self.boton_sumar = QPushButton("Sumar matrices")

        # --- Entradas (matrices pequeñas) ---
        self.tabla_a = _crear_tabla(editable=True)
        self.tabla_b = _crear_tabla(editable=True)

        # --- Resultado: 3 "páginas" en una pila (aviso / tabla / imagen) ---
        self.aviso = QLabel("Aquí aparecerá el resultado.")
        self.aviso.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabla_resultado = _crear_tabla(editable=False)
        self.etiqueta_imagen = EtiquetaImagen()
        self.pila = QStackedWidget()
        self.pila.addWidget(self.aviso)             # índice 0
        self.pila.addWidget(self.tabla_resultado)   # índice 1
        self.pila.addWidget(self.etiqueta_imagen)   # índice 2

        # --- Información del procesamiento ---
        self.info = QLabel("Dimensiones: —\nDispositivo del servidor: —\nTiempo: —")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def _armar_layout(self):
        fila_dimensiones = QHBoxLayout()
        fila_dimensiones.addWidget(QLabel("Filas:"))
        fila_dimensiones.addWidget(self.spin_filas)
        fila_dimensiones.addWidget(QLabel("Columnas:"))
        fila_dimensiones.addWidget(self.spin_columnas)
        fila_dimensiones.addSpacing(20)
        fila_dimensiones.addWidget(self.radio_manual)
        fila_dimensiones.addWidget(self.radio_servidor)
        fila_dimensiones.addStretch(1)

        fila_botones = QHBoxLayout()
        fila_botones.addWidget(self.boton_aleatorio)
        fila_botones.addStretch(1)
        fila_botones.addWidget(self.boton_sumar)

        # Panel izquierdo: matrices A y B (solo visible en modo manual)
        grupo_a = QGroupBox("Matriz A")
        QVBoxLayout(grupo_a).addWidget(self.tabla_a)
        grupo_b = QGroupBox("Matriz B")
        QVBoxLayout(grupo_b).addWidget(self.tabla_b)
        self.panel_entradas = QWidget()
        layout_entradas = QVBoxLayout(self.panel_entradas)
        layout_entradas.setContentsMargins(0, 0, 0, 0)
        layout_entradas.addWidget(grupo_a)
        layout_entradas.addWidget(grupo_b)

        # Panel derecho: resultado
        grupo_resultado = QGroupBox("Resultado C = A + B")
        QVBoxLayout(grupo_resultado).addWidget(self.pila)

        # QSplitter: el usuario puede arrastrar el divisor para repartir el espacio
        self.divisor = QSplitter(Qt.Orientation.Horizontal)
        self.divisor.addWidget(self.panel_entradas)
        self.divisor.addWidget(grupo_resultado)
        self.divisor.setStretchFactor(0, 1)
        self.divisor.setStretchFactor(1, 2)

        grupo_info = QGroupBox("Información del procesamiento")
        QVBoxLayout(grupo_info).addWidget(self.info)

        raiz = QVBoxLayout(self)
        raiz.addLayout(fila_dimensiones)
        raiz.addLayout(fila_botones)
        raiz.addWidget(self.divisor, stretch=1)
        raiz.addWidget(grupo_info)

    def _conectar_senales(self):
        self.spin_filas.valueChanged.connect(self._al_cambiar_dimensiones)
        self.spin_columnas.valueChanged.connect(self._al_cambiar_dimensiones)
        self.radio_manual.toggled.connect(self._actualizar_visibilidad)
        self.boton_aleatorio.clicked.connect(self._llenar_aleatorio)
        self.boton_sumar.clicked.connect(self._sumar)

    # ------------------------------------------------------------------
    # Reacción a cambios de dimensiones / origen
    # ------------------------------------------------------------------
    def _manual_permitido(self):
        return self.spin_filas.value() <= MAX_DIM_MANUAL and self.spin_columnas.value() <= MAX_DIM_MANUAL

    def _al_cambiar_dimensiones(self):
        filas, columnas = self.spin_filas.value(), self.spin_columnas.value()
        permitido = self._manual_permitido()

        self.radio_manual.setEnabled(permitido)
        if not permitido and self.radio_manual.isChecked():
            self.radio_servidor.setChecked(True)  # captura manual imposible: pasamos a "servidor"

        if permitido:  # solo dimensionamos las tablas de entrada cuando caben
            self._redimensionar(self.tabla_a, filas, columnas)
            self._redimensionar(self.tabla_b, filas, columnas)
        self._actualizar_visibilidad()

    def _actualizar_visibilidad(self):
        modo_manual = self.radio_manual.isChecked() and self._manual_permitido()
        self.panel_entradas.setVisible(modo_manual)
        self.boton_aleatorio.setEnabled(modo_manual)

    @staticmethod
    def _redimensionar(tabla, filas, columnas):
        """Ajusta filas/columnas conservando lo ya escrito; las celdas nuevas empiezan en 0."""
        tabla.setRowCount(filas)
        tabla.setColumnCount(columnas)
        for i in range(filas):
            for j in range(columnas):
                if tabla.item(i, j) is None:
                    tabla.setItem(i, j, QTableWidgetItem("0"))

    def _llenar_aleatorio(self):
        for tabla in (self.tabla_a, self.tabla_b):
            for i in range(tabla.rowCount()):
                for j in range(tabla.columnCount()):
                    tabla.item(i, j).setText(f"{random.uniform(0, 10):.2f}")

    # ------------------------------------------------------------------
    # Enviar solicitud
    # ------------------------------------------------------------------
    @staticmethod
    def _leer_tabla(tabla, nombre):
        """Convierte el contenido de un QTableWidget en lista de listas de float."""
        matriz = []
        for i in range(tabla.rowCount()):
            fila = []
            for j in range(tabla.columnCount()):
                item = tabla.item(i, j)
                texto = item.text().strip().replace(",", ".") if item else ""
                try:
                    valor = float(texto)
                    if not math.isfinite(valor):
                        raise ValueError
                except ValueError:
                    raise ValueError(f"Valor inválido en la matriz {nombre}, fila {i + 1}, columna {j + 1}: «{texto}»")
                fila.append(valor)
            matriz.append(fila)
        return matriz

    def _sumar(self):
        filas, columnas = self.spin_filas.value(), self.spin_columnas.value()
        manual = self.radio_manual.isChecked() and self._manual_permitido()
        matriz_a = matriz_b = None

        if manual:
            try:
                matriz_a = self._leer_tabla(self.tabla_a, "A")
                matriz_b = self._leer_tabla(self.tabla_b, "B")
            except ValueError as error:
                self.info.setText(f"Error: {error}")
                return

        self.boton_sumar.setEnabled(False)  # evita clics repetidos mientras espera
        self.info.setText("Enviando solicitud al servidor…")
        self.hilo = HiloSuma(self.cliente, filas, columnas, "valores" if manual else "generar", matriz_a, matriz_b)
        self.hilo.resultado.connect(self._mostrar_resultado)
        self.hilo.fallo.connect(self._mostrar_error)
        self.hilo.start()

    # ------------------------------------------------------------------
    # Mostrar respuesta
    # ------------------------------------------------------------------
    def _mostrar_resultado(self, datos):
        filas, columnas = datos["filas"], datos["columnas"]

        if datos["formato"] == "numerico":
            tabla = self.tabla_resultado
            tabla.setRowCount(filas)
            tabla.setColumnCount(columnas)
            for i, fila in enumerate(datos["resultado"]):
                for j, valor in enumerate(fila):
                    tabla.setItem(i, j, QTableWidgetItem(f"{valor:.4f}"))
            tabla.resizeColumnsToContents()
            self.pila.setCurrentIndex(1)
            extra = ""
        else:
            self.etiqueta_imagen.poner_imagen(datos["imagen_png"])
            self.pila.setCurrentIndex(2)
            extra = (f"\nImagen en escala de grises ({datos['imagen_ancho']}×{datos['imagen_alto']} px): "
                     f"valores {datos['valor_minimo']:.4f} (negro) a {datos['valor_maximo']:.4f} (blanco)")

        self.info.setText(
            f"Dimensiones procesadas: {filas}×{columnas}\n"
            f"Dispositivo del servidor: {datos['dispositivo']} ({datos['detalle']})\n"
            f"Tiempo de la suma: {datos['tiempo_suma_ms']:.3f} ms  |  "
            f"Tiempo total en el servidor (con conversiones y transferencias): {datos['tiempo_total_ms']:.1f} ms"
            f"{extra}"
        )
        self.boton_sumar.setEnabled(True)

    def _mostrar_error(self, mensaje):
        self.info.setText(f"Error: {mensaje}")
        self.boton_sumar.setEnabled(True)
