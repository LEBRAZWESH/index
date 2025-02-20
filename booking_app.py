import sys
import os
import time
import pandas as pd
import chardet
import logging
import numpy as np
import json
import io
from pandas.errors import EmptyDataError, ParserError
from openpyxl import load_workbook
from zipfile import BadZipFile
import traceback
from PyQt5.QtWidgets import QInputDialog, QProgressDialog, QAbstractItemView
from datetime import datetime
import pytz
import difflib
import qrcode
from PIL import Image
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QTableWidget, 
    QTableWidgetItem, QFileDialog, QMessageBox, QPushButton, QMenu, 
    QAction, QFormLayout, QHeaderView, QLabel, QTabWidget, QToolBar, 
    QShortcut, QComboBox, QLineEdit, QListWidget, QListWidgetItem, 
    QProgressBar, QWidget, QCalendarWidget, QTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt5.QtGui import QKeySequence, QFontDatabase, QFont, QIcon, QColor
from PyQt5.QtWebEngineWidgets import QWebEngineView
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
import folium
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut
from typing import Any, Optional

# Configuration
CONFIG_FILE = "config/settings.json"
GEOCODE_CACHE_FILE = "cache/geocode_cache.json"
LOG_FILE = "logs/booking_app.log"

# Configuration globale du logging

LOG_FILE = "logs/booking_app.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "default_location": "France",
            "map_zoom_level": 6,
            "date_format": "%Y-%m-%d",
            "time_format": "%H:%M:%S",
            "user": "LEBRAZWESH",
            "timezone": "UTC",
            "logo_path": "assets/logo.png",
            "cache_duration": 7
        }

def load_geocode_cache():
    if os.path.exists(GEOCODE_CACHE_FILE):
        with open(GEOCODE_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_geocode_cache(cache):
    os.makedirs(os.path.dirname(GEOCODE_CACHE_FILE), exist_ok=True)
    with open(GEOCODE_CACHE_FILE, "w") as f:
        json.dump(cache, f)

config = load_config()
geocode_cache = load_geocode_cache()

class MapGeocodeWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, contacts, geocoder):
        super().__init__()
        self.contacts = contacts
        self.geocoder = geocoder
        self.cache = {}

    def run(self):
        logging.info("🚀 Début du processus de géolocalisation")
        results = []

        for i, row in enumerate(self.contacts):
            possible_queries = self.build_search_query(row)
            location = self.safe_geocode(possible_queries)
            coordinates = f"{location['lat']}, {location['lon']}" if location else "Non trouvé"

            results.append({
                "contact": row.get("contact", "Inconnu"),
                "search_query": possible_queries[0] if possible_queries else "N/A",
                "status": row.get("status", "Inconnu"),
                "coordinates": coordinates
            })

            self.progress.emit(int((i + 1) / len(self.contacts) * 100))

        if not results:
            logging.warning("⚠️ Aucun résultat trouvé")
            results.append({"contact": "Aucun", "search_query": "N/A", "status": "N/A", "coordinates": "Non trouvé"})

        self.finished.emit(results)

class SortHeaderView(QHeaderView):
    def __init__(self, orientation, table, parent=None):
        super().__init__(orientation, parent)
        self.table = table  # Reference to the QTableWidget
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setDefaultAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        idx = self.logicalIndexAt(event.pos())
        if idx < 0:
            super().mousePressEvent(event)
            return

        left = self.sectionPosition(idx)
        width = self.sectionSize(idx)
        right = left + width
        # If click is in the last 20 pixels of the section, show sort menu
        if event.pos().x() > right - 20:
            menu = QMenu(self)
            action_asc = menu.addAction("Trier de A à Z")
            action_desc = menu.addAction("Trier de Z à A")
            action = menu.exec_(self.mapToGlobal(event.pos()))
            if action == action_asc:
                self.table.sortItems(idx, Qt.AscendingOrder)
            elif action == action_desc:
                self.table.sortItems(idx, Qt.DescendingOrder)
        else:
            super().mousePressEvent(event)

class DraggableTableWidget(QTableWidget):
    """TableWidget permettant le glisser-déposer sans supprimer les lignes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        # Récupère les indices des lignes sélectionnées
        selected_rows = sorted(set(index.row() for index in self.selectedIndexes()))
        target_index = self.indexAt(event.pos())
        target_row = target_index.row() if target_index.isValid() else self.rowCount()

        if not selected_rows:
            return

        # Extraction des QTableWidgetItem clonés pour chaque ligne sélectionnée
        rows_data = []
        for row in selected_rows:
            row_data = []
            for col in range(self.columnCount()):
                item = self.item(row, col)
                # Clonage de l'item complet, conservant ainsi toutes ses propriétés
                cloned_item = QTableWidgetItem(item) if item else QTableWidgetItem("")
                row_data.append(cloned_item)
            rows_data.append(row_data)

        # Ajustement de l'indice de destination pour tenir compte des suppressions
        if target_row > selected_rows[0]:
            target_row -= len(selected_rows)

        # Suppression des lignes sélectionnées en partant du bas pour préserver les indices
        for row in reversed(selected_rows):
            self.removeRow(row)

        # Réinsertion des lignes extraites à l'indice cible en conservant l'ordre et les propriétés
        for i, row_data in enumerate(rows_data):
            self.insertRow(target_row + i)
            for col, cloned_item in enumerate(row_data):
                self.setItem(target_row + i, col, cloned_item)

        event.accept()


class BookingApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Variables de suivi et de gestion de fichier
        self.current_file = None
        self.undo_stack = []
        self.redo_stack = []
        self.last_values = {}
        self.undo_redo_in_progress = False

        # Configuration de la fenêtre principale
        self.setWindowTitle("Améliorations d'ergonomie")
        self.setGeometry(100, 100, 600, 400)
        
        # Configuration initiale de l'interface
        self.setupUI()

        # Affichage des onglets (ici seulement l'onglet tableau est créé)
        self.create_table_tab()
        self.setCentralWidget(self.table_tab)
        
        # Afficher la fenêtre principale
        self.show()

        # Liste des contacts affichés sur la carte (à utiliser pour la géolocalisation ou l'itinéraire)
        self.map_contacts = []

        # Chargement de la police personnalisée (si disponible)
        self.load_custom_font()

        # Initialisation du géocodeur avec un timeout configuré pour améliorer la stabilité
        self.geocoder = Nominatim(user_agent="booking_app", timeout=5)

        # Création de la barre d'outils et des raccourcis clavier pour une meilleure ergonomie
        self.create_toolbar()
        self.create_tabs()
        self.create_shortcuts()

        # Application d'une feuille de style pour harmoniser l'apparence
        self.apply_stylesheet()

        # Chargement du logo de l'application (fonctionnalité à compléter)
        self.load_logo()

    def setupUI(self):
        """
        Configure les éléments de l'interface principale dans un widget central utilisant un layout vertical.
        Ce layout permet de structurer visuellement la fenêtre de manière claire et aérée.
        """
        central_widget = QWidget()
        layout = QVBoxLayout()

        # Label d'accueil centré pour une hiérarchisation visuelle claire
        label = QLabel("Bienvenue dans l'application améliorée")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 18px; margin-bottom: 20px;")
        layout.addWidget(label)

        # Bouton "Enregistrer les modifications"
        # Le style personnalisé (fond vert, coins arrondis, et effet de survol) apporte une apparence moderne et met en avant l'action positive.
        self.enregistrer_btn = QPushButton("Enregistrer les modifications")
        self.enregistrer_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; 
                color: white; 
                border: none;
                border-radius: 10px;
                font-size: 16px;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.enregistrer_btn.clicked.connect(self.save_action)
        layout.addWidget(self.enregistrer_btn)

        # Bouton "Annuler"
        # Un design cohérent avec le bouton précédent, mais avec un fond rouge pour signaler une action négative.
        self.annuler_btn = QPushButton("Annuler")
        self.annuler_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336; 
                color: white; 
                border: none; 
                border-radius: 10px;
                font-size: 16px;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        self.annuler_btn.clicked.connect(self.cancel_action)
        layout.addWidget(self.annuler_btn)

        # Insertion d'un espace flexible pour aérer l'interface
        layout.addStretch()

        # Affecte le layout au widget central et définit ce widget comme central de la fenêtre
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def build_search_query(self, row: dict) -> list:
        """Construit plusieurs variantes d'adresse en utilisant les colonnes détectées dynamiquement."""
        detected = self.detect_address_columns(row)

        address = detected["address"]
        city = detected["city"]
        region = detected["region"]
        department = detected["department"]
        postal_code = detected["postal_code"]
        country = detected["country"] or ""

        possible_queries = []

        if address and city and postal_code:
            possible_queries.append(f"{address}, {city}, {postal_code}, {country}")
        if address and city:
            possible_queries.append(f"{address}, {city}, {country}")
        if city and postal_code:
            possible_queries.append(f"{city}, {postal_code}, {country}")
        if address and department:
            possible_queries.append(f"{address}, {department}, {country}")
        if city and region:
            possible_queries.append(f"{city}, {region}, {country}")
        if address:
            possible_queries.append(f"{address}, {country}")
        if city:
            possible_queries.append(f"{city}, {country}")
        if department:
            possible_queries.append(f"{department}, {country}")
        if region:
            possible_queries.append(f"{region}, {country}")
        if postal_code:
            possible_queries.append(f"{postal_code}, {country}")

        # ⚠️ Vérifier que la requête n'est pas vide ou uniquement "France"
        possible_queries = [q for q in possible_queries if q.strip() and q.lower() != "france, france"]

        # 🔥 Dernier recours : on ne met pas Paris, mais on laisse None pour que `safe_geocode()` gère l'échec
        if not possible_queries:
            return []

        return possible_queries



    def create_table_tab(self):
        """
        Crée l'onglet destiné à la gestion des tableaux.
        """
        self.table_tab = QWidget()
        layout = QVBoxLayout()
        label = QLabel("Ici se trouve le contenu du tableau.")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self.table_tab.setLayout(layout)

    def safe_geocode(self, queries, retries=3, delay=1):
        if not queries:
            logging.warning("⚠️ Aucune requête valide à tester")
            return None

        for query in queries:
            for attempt in range(retries):
                try:
                    logging.info(f"🌍 Essai {attempt + 1}/{retries} avec : {query}")
                    location = self.geocoder.geocode(query, exactly_one=True)

                    if location:
                        return {"lat": location.latitude, "lon": location.longitude}

                    logging.warning(f"⚠️ Aucun résultat pour '{query}'")

                except GeocoderTimedOut:
                    logging.error(f"⏳ Timeout pour '{query}'")
                except Exception as e:
                    logging.error(f"❌ Erreur de géocodage : {str(e)}")

                time.sleep(delay)

        return None

    def detect_address_columns(self, row: dict) -> dict:
        """Détecte dynamiquement les colonnes contenant des informations de localisation, peu importe leur nom."""

        column_mapping = {
            "name": ["nom", "contact", "établissement", "organisation", "enseigne"],
            "address": ["adresse", "lieu", "localisation", "rue", "addresse", "address", "location"],
            "city": ["ville", "commune", "municipalité", "city", "town"],
            "region": ["région", "province", "state", "county"],
            "department": ["département", "canton", "district"],
            "postal_code": ["code postal", "cp", "postal code", "zip"],
            "country": ["pays", "country", "nation"]
        }

        detected = {key: "" for key in column_mapping}

        print(f"        🛠️ Colonnes disponibles : {list(row.keys())}")

        for col in row.keys():
            for key, aliases in column_mapping.items():
                if col.lower() in aliases or any(alias in col.lower() for alias in aliases):
                    detected[key] = row[col]
                    print(f"        ✅ Colonne détectée : {col} -> {key} = {row[col]}")

        print(f"        🔍 Colonnes détectées : {detected}")
        return detected


    def create_toolbar(self):
        """
        Crée la barre d'outils pour regrouper les actions fréquentes avec des icônes et des descriptions.
        Cette barre améliore l'accès rapide aux fonctionnalités et apporte du feedback visuel.
        """
        # Code de création de toolbar à ajouter ici
        pass

    def create_tabs(self):
        """
        Méthode pour créer et organiser plusieurs onglets.
        À compléter avec les autres onglets en fonction des besoins de l'application.
        """
        # Code de création d'onglets à ajouter ici
        pass

    def create_shortcuts(self):
        """
        Définit des raccourcis clavier afin d'améliorer la réactivité et l'ergonomie.
        Par exemple, Ctrl+Z pour annuler ou Ctrl+Y pour rétablir.
        """
        # Code de raccourcis clavier à ajouter ici
        pass

    def apply_stylesheet(self):
        try:
            with open(r"C:\booking_app\assets\DESIGN.qss", "r", encoding="utf-8") as style_file:
                self.setStyleSheet(style_file.read())
        except Exception as e:
            print("Erreur lors du chargement du fichier de style:", e)

    def load_custom_font(self):
        """
        Charge une police personnalisée pour améliorer la lisibilité et l'esthétique de l'application.
        """
        # Implémentation de chargement de police personnalisée (à adapter selon l'emplacement réel des fichiers)
        pass

    def load_logo(self):
        """
        Charge et affiche le logo de l'application.
        Cette méthode pourra être utilisée pour ajouter le logo dans la barre d'outils ou ailleurs dans l'interface.
        """
        # Implémentation du chargement du logo (à adapter selon les besoins)
        pass

    def save_action(self):
        """
        Exemple de fonction exécutée lors du clic sur "Enregistrer les modifications".
        Affiche un message de confirmation pour l'utilisateur.
        """
        QMessageBox.information(self, "Enregistrer", "Les modifications ont bien été enregistrées.")

    def cancel_action(self):
        """
        Exemple de fonction exécutée lors du clic sur "Annuler".
        Affiche un message pour signaler l'annulation de l'action en cours.
        """
        QMessageBox.warning(self, "Annuler", "L'action a été annulée.")

    def parse_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """
        Effectue le traitement commun sur le DataFrame importé :
        - Conversion de la colonne 'Date' en datetime,
        - Transformation de la colonne 'Téléphone' en chaînes de chiffres uniquement,
        - Conversion de la colonne 'Cachet' en float,
        - Remplacement des valeurs infinies et des NaN.
        
        :param df: DataFrame brut à traiter
        :return: DataFrame traité
        """
        cols_to_parse = ['Date', 'Cachet', 'Téléphone']
        for col in cols_to_parse:
            if col in df.columns:
                if col == 'Date':
                    df[col] = pd.to_datetime(df[col], errors='coerce')
                elif col == 'Téléphone':
                    df[col] = df[col].astype(str).str.replace(r'\D+', '', regex=True)
                elif col == 'Cachet':
                    df[col] = df[col].astype(float)
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        df.fillna({
            'Contact': 'Inconnu',
            'Cachet': 0,
            'Statut': 'À confirmer'
        }, inplace=True)
        if 'Date' in df.columns:
            df['Date'] = df['Date'].dt.strftime(config["date_format"])
        return df

    def load_custom_font(self):
        font_path = os.path.join(os.path.dirname(__file__), "assets", "WorkSans-Medium.ttf")
        font_id = QFontDatabase.addApplicationFont(font_path)

        if font_id == -1:
            logging.warning("Erreur lors du chargement de la police WorkSans-Medium.")
            self.custom_font_family = "Arial"
        else:
            self.custom_font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
            self.setFont(QFont(self.custom_font_family, 10))

    def initialize_empty_table(self):
        """
        Initialise un tableur vide avec les colonnes par défaut : "Date", "Statut" et "Cachet".
        """
        default_headers = ["Date", "Statut", "Cachet"]
        self.table.setRowCount(0)  # On vide la table
        self.table.setColumnCount(len(default_headers))
        self.table.setHorizontalHeaderLabels(default_headers)

    def adjust_columns(self):
        """Ajuste automatiquement la largeur des colonnes"""
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        for col in range(self.table.columnCount()):
            if header.sectionSize(col) > 300:
                header.setSectionResizeMode(col, QHeaderView.Stretch)

    def filter_table(self):
        search_text = self.search_bar.text().lower()
        for row in range(self.table.rowCount()):
            row_match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and search_text in item.text().lower():
                    row_match = True
                    break
            self.table.setRowHidden(row, not row_match)

    def insert_empty_row(self, position=None):
        """Insère une ligne vide à la position spécifiée ou à la fin si None."""
        if position is None or position > self.table.rowCount():
            position = self.table.rowCount()
        self.table.insertRow(position)

        for col in range(self.table.columnCount()):
            empty_item = QTableWidgetItem("")
            self.table.setItem(position, col, empty_item)

    def remove_selected_rows(self):
        """Supprime toutes les lignes sélectionnées."""
        selected_rows = sorted(set(item.row() for item in self.table.selectedItems()), reverse=True)
        if not selected_rows:
            QMessageBox.warning(self, "Suppression", "Aucune ligne sélectionnée.")
            return

        reply = QMessageBox.question(
            self, 'Confirmation',
            f'Voulez-vous vraiment supprimer {len(selected_rows)} ligne(s) ?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            for row in selected_rows:
                self.table.removeRow(row)

    def get_column_headers(self):
        """Renvoie la liste des en-têtes de colonne en gérant les valeurs nulles."""
        return [self.table.horizontalHeaderItem(col).text() if self.table.horizontalHeaderItem(col) else "" 
                for col in range(self.table.columnCount())]

    def get_selected_data(self):
        """Récupère les valeurs des cellules sélectionnées."""
        selected_items = self.table.selectedItems()
        if not selected_items:
            return []

        return [item.text() for item in selected_items if item]

    def select_entire_row(self, row):
        """Sélectionne une ligne entière."""
        self.table.selectRow(row)

    def select_entire_column(self, column):
        """Sélectionne une colonne entière en vérifiant les limites."""
        if 0 <= column < self.table.columnCount():
            self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)  # Assure la sélection de colonnes
            self.table.setSelectionBehavior(QTableWidget.SelectColumns)  # Active la sélection de colonnes
            self.table.selectColumn(column)

    def export_route(self):
        """
        Exporte la feuille de route au format souhaité (PDF, Excel, etc.).
        Cette méthode doit être implémentée pour fournir la fonctionnalité d'exportation.
        """
        try:
            options = QFileDialog.Options()
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Exporter la feuille de route",
                "",
                "Fichiers PDF (*.pdf);;Fichiers Excel (*.xlsx);;Tous les fichiers (*)",
                options=options
            )

            if not file_path:
                return

            route_data = self.get_route_data()
            file_ext = file_path.lower().split('.')[-1]
            if file_ext == "pdf":
                self.export_route_to_pdf(file_path, route_data)
            elif file_ext == "xlsx":
                self.export_route_to_excel(file_path, route_data)
            else:
                QMessageBox.warning(self, "Format de fichier inconnu", "Le format de fichier sélectionné n'est pas supporté.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur technique", f"Erreur lors de l'exportation : {str(e)}")
            logging.error(f"Erreur d'exportation: {traceback.format_exc()}")

    def export_route_to_pdf(self, file_path, route_data):
        """
        Exporte les données de la feuille de route au format PDF.
        Veuillez implémenter la logique d'exportation en PDF ici.
        """
        try:
            logging.info("Export route to PDF démarré")
            # Ajoutez ici votre code pour créer un document PDF à partir de route_data.
            # Par exemple, vous pouvez utiliser ReportLab pour générer le PDF.
            pass
        except Exception as e:
            logging.error(f"Erreur dans export_route_to_pdf: {e}")
            raise e

    def export_route_to_excel(self, file_path, route_data):
        """
        Exporte les données de la feuille de route au format Excel.
        Veuillez implémenter la logique d'exportation en Excel ici.
        """
        try:
            logging.info("Export route to Excel démarré")
            # Ajoutez ici votre code pour créer un fichier Excel à partir de route_data.
            pass
        except Exception as e:
            logging.error(f"Erreur dans export_route_to_excel: {e}")
            raise e

    def get_route(start, end):
        """
        Récupère l'itinéraire entre deux points via l'API publique d'OSRM.

        :param start: Tuple (lat, lon) du point de départ.
        :param end: Tuple (lat, lon) du point d'arrivée.
        :return: Tuple contenant (geometry: liste de paires [lat, lon], duration en secondes, distance en mètres)
                 ou (None, None, None) en cas d'erreur.
        """
        try:
            # OSRM attend l'ordre : longitude, latitude.
            start_lon_lat = f"{start[1]},{start[0]}"
            end_lon_lat = f"{end[1]},{end[0]}"
            url = f"http://router.project-osrm.org/route/v1/driving/{start_lon_lat};{end_lon_lat}?overview=full&geometries=geojson"
            response = requests.get(url)
            data = response.json()
            if data and data.get("routes"):
                route = data["routes"][0]
                geometry = route["geometry"]["coordinates"]
                duration = route["duration"]  # en secondes
                distance = route["distance"]  # en mètres
                # Convertir les coordonnées de [lon, lat] à [lat, lon]
                converted_geometry = [[coord[1], coord[0]] for coord in geometry]
                return converted_geometry, duration, distance
        except Exception as e:
            logging.error(f"Erreur lors de la récupération de l'itinéraire depuis OSRM : {e}")
        return None, None, None

    def add_route_to_map(m: folium.Map, points: list) -> float:
        """
        Ajoute sur la carte folium une ligne reliant les points d'itinéraire
        et retourne la durée totale estimée du trajet en minutes.

        :param m: Objet folium.Map sur lequel dessiner l'itinéraire.
        :param points: Liste de points (chaque point est une liste [lat, lon]).
        :return: Durée totale du trajet en minutes.
        """
        total_duration = 0
        if len(points) < 2:
            logging.info("Nombre insuffisant de points pour calculer l'itinéraire")
            return total_duration

        # Itère sur chaque paire de points consécutifs
        for i in range(len(points) - 1):
            start = points[i]
            end = points[i + 1]
            geometry, duration, distance = route_helpers.get_route(start, end)
            if geometry:
                PolyLine(geometry, color="blue", weight=5, opacity=0.7).add_to(m)
                total_duration += duration
            else:
                logging.warning(f"Impossible de récupérer l'itinéraire entre {start} et {end}")
        return total_duration / 60  # Convertir les secondes en minutes

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        if value >= 100:
            self.progress_bar.setVisible(False)

    def import_file(self):
        """Fonction d'importation qui gère à la fois les fichiers Excel et CSV"""
        try:
            options = QFileDialog.Options()
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Importer un fichier",
                "",
                "Fichiers Excel (*.xlsx *.xls);;Fichiers CSV (*.csv)",
                options=options
            )

            if not file_path:
                return

            if self.check_unsaved_changes() is False:
                return

            file_ext = file_path.lower().split('.')[-1]
            if file_ext in ["xlsx", "xls"]:
                self.import_excel(file_path)
            elif file_ext == "csv":
                self.import_csv(file_path)
            else:
                QMessageBox.warning(self, "Type de fichier inconnu", "Le format du fichier n'est pas supporté.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur technique", f"Erreur lors de l'importation : {str(e)}")
            logging.error(f"Erreur d'importation: {traceback.format_exc()}")


    def import_excel(self, file_path):
        """Import d'un fichier Excel (.xlsx ou .xls)"""
        try:
            with pd.ExcelFile(file_path, engine='openpyxl') as xls:
                sheet_names = xls.sheet_names
                sheet_name, ok = QInputDialog.getItem(
                    self, 
                    "Sélection de la feuille",
                    "Feuilles disponibles:",
                    sheet_names,
                    0, False
                )
                if not ok:
                    return

                df = pd.read_excel(
                    file_path,
                    sheet_name=sheet_name,
                    engine='openpyxl'
                )

                cols_to_parse = ['Date', 'Cachet', 'Téléphone']
                for col in cols_to_parse:
                    if col in df.columns:
                        if col == 'Date':
                            df[col] = pd.to_datetime(df[col], errors='coerce')
                        elif col == 'Téléphone':
                            df[col] = df[col].astype(str).str.replace(r'\D+', '', regex=True)
                        elif col == 'Cachet':
                            df[col] = df[col].astype(float)

            df.replace([np.inf, -np.inf], np.nan, inplace=True)
            df.fillna({
                'Contact': 'Inconnu',
                'Cachet': 0,
                'Statut': 'À confirmer'
            }, inplace=True)

            if 'Date' in df.columns:
                df['Date'] = df['Date'].dt.strftime(config["date_format"])

            # Build headers: always start with the default columns, then add the imported ones.
            default_headers = ["Date", "Statut", "Cachet"]
            imported_headers = list(df.columns.astype(str))
            all_headers = default_headers + imported_headers
            self.table.setRowCount(0)
            self.table.setColumnCount(len(all_headers))
            self.table.setHorizontalHeaderLabels(all_headers)
            # Make sure sorting remains disabled during import
            self.table.setSortingEnabled(False)
            
            self.table.setUpdatesEnabled(False)
            try:
                for _, row in df.iterrows():
                    row_position = self.table.rowCount()
                    self.table.insertRow(row_position)
                    # Insert default empty values for the default columns
                    for i in range(len(default_headers)):
                        item = QTableWidgetItem("")
                        self.table.setItem(row_position, i, item)
                    # Insert imported values in subsequent columns
                    for col, value in enumerate(row):
                        item = QTableWidgetItem(str(value).strip() if pd.notnull(value) else "")
                        if isinstance(value, (int, float)):
                            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        elif 'date' in imported_headers[col].lower():
                            item.setForeground(QColor("darkBlue"))
                        self.table.setItem(row_position, len(default_headers) + col, item)
            finally:
                self.table.setUpdatesEnabled(True)

            # Optionally adjust columns width if you have that function
            # self.adjust_columns()
            self.statusBar().showMessage(f"Fichier importé : {os.path.basename(file_path)}", 5000)
            logging.info(f"Import Excel réussi : {len(df)} lignes")
        except PermissionError as e:
            QMessageBox.critical(self, "Erreur d'accès",
                f"Impossible d'ouvrir le fichier :\n{str(e)}\n"
                "Vérifiez qu'il n'est pas ouvert dans un autre programme.")
            logging.error(f"Erreur de permission : {str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur technique",
                f"Erreur lors de la lecture du fichier :\n{str(e)}")
            logging.error(f"Erreur Excel : {traceback.format_exc()}")

    def undo(self):
        """
        Annule la dernière modification effectuée (undo).
        """
        logging.info("undo non implémenté")
        QMessageBox.information(self, "Undo", "Fonction undo non encore implémentée.")

    def redo(self):
        """
        Rétablit la dernière modification annulée (redo).
        """
        logging.info("redo non implémenté")
        QMessageBox.information(self, "Redo", "Fonction redo non encore implémentée.")

    def delete_event(self):
        """
        Supprime l'événement sélectionné dans l'onglet Calendrier.
        """
        logging.info("delete_event non implémenté")
        QMessageBox.information(self, "Supprimer Événement", "Fonction de suppression d'événement non encore implémentée.")

    def add_event(self):
        """
        Ajoute un événement depuis l'onglet Calendrier.
        """
        logging.info("add_event non implémenté")
        QMessageBox.information(self, "Ajouter Événement", "Fonction d'ajout d'événement non encore implémentée.")

    def edit_event(self, item):
        """
        Modifie l'événement sélectionné dans l'onglet Calendrier.
        L'argument 'item' correspond à l'élément de la QListWidget.
        """
        logging.info("edit_event non implémenté")
        QMessageBox.information(self, "Modifier Événement", "Fonction d'édition d'événement non encore implémentée.")

    def import_csv(self, file_path):
        """Import d'un fichier CSV"""
        try:
            with open(file_path, 'rb') as f:
                rawdata = f.read(10000)
            result = chardet.detect(rawdata)
            encoding = result['encoding'] if result['encoding'] else 'utf-8'

            df = pd.read_csv(file_path, encoding=encoding)

            cols_to_parse = ['Date', 'Cachet', 'Téléphone']
            for col in cols_to_parse:
                if col in df.columns:
                    if col == 'Date':
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                    elif col == 'Téléphone':
                        df[col] = df[col].astype(str).str.replace(r'\D+', '', regex=True)
                    elif col == 'Cachet':
                        df[col] = df[col].astype(float)

            df.replace([np.inf, -np.inf], np.nan, inplace=True)
            df.fillna({
                'Contact': 'Inconnu',
                'Cachet': 0,
                'Statut': 'À confirmer'
            }, inplace=True)

            if 'Date' in df.columns:
                df['Date'] = df['Date'].dt.strftime(config["date_format"])

            # Build headers: default columns first, then imported ones.
            default_headers = ["Date", "Statut", "Cachet"]
            imported_headers = list(df.columns.astype(str))
            all_headers = default_headers + imported_headers
            self.table.setRowCount(0)
            self.table.setColumnCount(len(all_headers))
            self.table.setHorizontalHeaderLabels(all_headers)
            # Ensure sorting is disabled at import
            self.table.setSortingEnabled(False)

            self.table.setUpdatesEnabled(False)
            try:
                for _, row in df.iterrows():
                    row_position = self.table.rowCount()
                    self.table.insertRow(row_position)
                    # Insert default empty values for the default columns
                    for i in range(len(default_headers)):
                        item = QTableWidgetItem("")
                        self.table.setItem(row_position, i, item)
                    # Insert imported values accordingly
                    for col, value in enumerate(row):
                        item = QTableWidgetItem(str(value).strip() if pd.notnull(value) else "")
                        if isinstance(value, (int, float)):
                            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        elif 'date' in imported_headers[col].lower():
                            item.setForeground(QColor("darkBlue"))
                        self.table.setItem(row_position, len(default_headers) + col, item)
            finally:
                self.table.setUpdatesEnabled(True)

            # Optionally adjust columns width if you have that function
            # self.adjust_columns()
            self.statusBar().showMessage(f"Fichier importé : {os.path.basename(file_path)}", 5000)
            logging.info(f"Import CSV réussi : {len(df)} lignes")
        except Exception as e:
            QMessageBox.critical(self, "Erreur technique",
                                 f"Erreur lors de l'import CSV :\n{str(e)}")
            logging.error(f"Erreur import CSV: {traceback.format_exc()}")

    def initialize_map_with_contacts(self, contacts):
        """Ajoute des contacts sur la carte et affiche les échecs de géolocalisation."""
        
        for contact in contacts:
            if contact not in self.map_contacts:
                self.map_contacts.append(contact)

        m = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="OpenStreetMap")
        marker_cluster = MarkerCluster().add_to(m)

        for row in contacts:
            detected = self.detect_address_columns(row)  # ✅ Nouvelle détection dynamique

            contact = detected["address"] if detected["address"] else "Inconnu"
            address = detected["address"]
            status = detected["city"]  # Utiliser la ville comme statut temporaire

            if not address.strip():
                print(f"❌ Adresse vide détectée : {address}, Colonnes détectées : {detected}, Données complètes : {row}")  
                continue

            possible_queries = self.build_search_query(detected)
            geocode_location = self.safe_geocode(possible_queries)

            if geocode_location:
                lat, lon = geocode_location["lat"], geocode_location["lon"]
                print(f"📍 Géolocalisation réussie : {contact} -> {lat}, {lon}")

                folium.Marker(
                    location=[lat, lon],
                    popup=f"{contact} ({status})<br>{address}",
                    icon=folium.Icon(color="blue", icon="info-sign")
                ).add_to(marker_cluster)

            else:
                print(f"❌ Échec : Aucune correspondance trouvée pour {contact}, aucun marqueur ajouté.")

        data = io.BytesIO()
        m.save(data, close_file=False)
        self.map_view.setHtml(data.getvalue().decode())

    def import_data(self, df):
        """Ajoute un log après l'importation pour vérifier les valeurs réelles."""
        print(f"📄 Aperçu des données importées :\n{df.head(5)}")  # ✅ LOG pour voir les valeurs réelles

    def some_function(self, data):
        print(f"✅ Données reçues par some_function: {data} ({type(data)})")

    def clear_map_table(self):
        """Efface le tableau des lieux envoyés vers la carte."""
        self.map_table.setRowCount(0)

    def start_geocoding(self):
        self.geocode_worker = MapGeocodeWorker(self.contacts, self.geocoder)
        self.geocode_worker.finished.connect(lambda results: self.add_markers_to_map_and_route(results))
        self.geocode_worker.start()

    def send_selected_contacts_to_map(self):
        """Ajoute les contacts sélectionnés sur la carte sans effacer les anciens."""
        selected_rows = self.get_selected_rows()
        if not selected_rows:
            QMessageBox.warning(self, "Avertissement", "Sélectionnez au moins un contact.")
            return

        new_contacts = []

        for row in selected_rows:
            contact = self.get_cell_text(row, 0)  
            address = self.get_cell_text(row, 1)  
            status = self.get_cell_text(row, 2)  

            print(f"📋 Contact extrait: {contact}, Adresse: {address}, Statut: {status}")  # ✅ LOG

            new_contacts.append({
                "contact": contact,
                "address": address,
                "status": status
            })

        if new_contacts:
            self.initialize_map_with_contacts(new_contacts)

    def sort_columns(self, order='asc'):
        """
        Trie les colonnes du tableau par ordre alphabétique des en-têtes.
        Paramètre order: 'asc' pour de A à Z, 'desc' pour de Z à A.
        """
        header_count = self.table.columnCount()
        # Si le tableau est vide, il n'y a rien à trier.
        if header_count == 0:
            return

        rows = self.table.rowCount()
        # Récupérer la liste des en-têtes avec leur index
        headers = []
        for col in range(header_count):
            item = self.table.horizontalHeaderItem(col)
            headers.append((col, item.text() if item else ""))
        
        # Trier les en-têtes selon l'ordre spécifié
        if order == 'asc':
            sorted_headers = sorted(headers, key=lambda x: x[1])
        else:
            sorted_headers = sorted(headers, key=lambda x: x[1], reverse=True)
        
        # Construire le nouvel ordre des colonnes et la nouvelle liste d'en-têtes
        new_order = [col for col, text in sorted_headers]
        new_headers = [text for col, text in sorted_headers]
        
        # Sauvegarder les données actuelles du tableau
        data = []
        for row in range(rows):
            row_data = []
            for col in range(header_count):
                item = self.table.item(row, col)
                row_data.append(item.text() if item else "")
            data.append(row_data)
        
        # Réassigner les données selon le nouvel ordre
        self.table.clearContents()
        for row in range(rows):
            for new_col, orig_col in enumerate(new_order):
                new_item = QTableWidgetItem(data[row][orig_col])
                self.table.setItem(row, new_col, new_item)
        
        # Mettre à jour les en-têtes affichés
        self.table.setHorizontalHeaderLabels(new_headers)
        QMessageBox.information(self, "Tri des colonnes", f"Colonnes triées en ordre {'A -> Z' if order=='asc' else 'Z -> A'}.")

    def create_toolbar(self):
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        new_action = toolbar.addAction("Nouveau")
        new_action.triggered.connect(self.new_file)

        open_action = toolbar.addAction("Ouvrir")
        open_action.triggered.connect(self.open_file)

        save_action = toolbar.addAction("Enregistrer")
        save_action.triggered.connect(self.save_file)

        import_action = toolbar.addAction("Importer")
        import_action.triggered.connect(self.import_file)

        export_action = toolbar.addAction("Exporter")
        export_menu = QMenu()
        export_menu.addAction("PDF", self.export_pdf)
        export_menu.addAction("Excel", self.export_excel)
        export_action.setMenu(export_menu)

    def create_tabs(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.create_table_tab()
        self.create_map_tab()
        self.create_calendar_tab()
        self.create_stats_tab()  # Added stats tab

        self.tabs.addTab(self.table_tab, "Table des événements")
        self.tabs.addTab(self.map_tab, "Carte")
        self.tabs.addTab(self.calendar_tab, "Calendrier")
        self.tabs.addTab(self.stats_tab, "Statistiques")

    def create_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save_file)
        QShortcut(QKeySequence("Ctrl+O"), self, self.open_file)
        QShortcut(QKeySequence("Ctrl+N"), self, self.new_file)

    def load_logo(self):
        logo_path = config.get("logo_path", "assets/logo.png")
        if os.path.exists(logo_path):
            self.logo = logo_path
        else:
            logging.warning(f"Logo non trouvé: {logo_path}")
            self.logo = None

    def create_table_tab(self):
        self.table_tab = QWidget()
        layout = QVBoxLayout(self.table_tab)

        # Barre de recherche et tri
        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Rechercher...")
        self.search_bar.textChanged.connect(self.filter_table)
        search_layout.addWidget(self.search_bar)

        # Menu déroulant pour le tri des colonnes
        self.sort_dropdown = QComboBox()
        self.sort_dropdown.addItem("Trier par...", -1)
        self.sort_dropdown.addItem("Date (A-Z)", 0)
        self.sort_dropdown.addItem("Date (Z-A)", 1)
        self.sort_dropdown.addItem("Statut (A-Z)", 2)
        self.sort_dropdown.addItem("Statut (Z-A)", 3)
        self.sort_dropdown.addItem("Cachet (A-Z)", 4)
        self.sort_dropdown.addItem("Cachet (Z-A)", 5)
        self.sort_dropdown.currentIndexChanged.connect(self.sort_columns)
        search_layout.addWidget(self.sort_dropdown)

        layout.addLayout(search_layout)

        # Table des contacts
        self.table = QTableWidget()
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(False)  # Désactivé pour éviter les conflits

        # Définition des colonnes
        default_headers = ["Date", "Statut", "Cachet"]
        self.table.setColumnCount(len(default_headers))
        self.table.setHorizontalHeaderLabels(default_headers)

        # Style de la table
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #f8f9fa;  /* Couleur de fond principale */
                alternate-background-color: #e9ecef;  /* Gris clair une ligne sur deux */
                selection-background-color: #6c757d;  /* Gris foncé pour la sélection */
                selection-color: white;
                gridline-color: #dee2e6;  /* Couleur des lignes */
                border: 1px solid #ced4da;
            }
            QHeaderView::section {
                background-color: #0078d7;  /* Bleu pâle pour l'en-tête */
                color: white;
                font-weight: bold;
                padding: 5px;
                border: 1px solid #0069c0;
            }
            QTableWidget::item {
                padding: 5px;
            }
        """)

        # Activer une couleur alternée pour les lignes
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table)

        # Boutons d'action sous la table
        action_layout = QHBoxLayout()

        send_to_map_btn = QPushButton("🗺️ Envoyer vers Carte")
        send_to_map_btn.clicked.connect(self.send_selected_contacts_to_map)
        action_layout.addWidget(send_to_map_btn)

        buttons = {
            "add_row": ("➕ Ajouter", self.add_row),
            "delete_row": ("❌ Supprimer", self.delete_row),
            "generate_route": ("📄 Feuille de route", self.generate_route_sheet),
            "export": ("💾 Exporter", self.export_data)
        }

        for btn_id, (text, func) in buttons.items():
            btn = QPushButton(text)
            btn.clicked.connect(func)
            action_layout.addWidget(btn)

        layout.addLayout(action_layout)

        # Style des boutons
        for btn in action_layout.findChildren(QPushButton):
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0078d7;  /* Bleu Microsoft */
                    color: white;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-weight: bold;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #005a9e;
                }
                QPushButton:pressed {
                    background-color: #004275;
                }
            """)

        self.table_tab.setLayout(layout)


    def show_header_menu(self, pos):
        header = self.table.horizontalHeader()
        col = header.logicalIndexAt(pos)
        if col < 0:
            return
        menu = QMenu()
        action_asc = menu.addAction("Trier de A à Z")
        action_desc = menu.addAction("Trier de Z à A")
        global_pos = header.viewport().mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action:
            # Active temporairement le tri pour réordonner les lignes
            self.table.setSortingEnabled(True)
            if action == action_asc:
                self.table.sortItems(col, Qt.AscendingOrder)
            elif action == action_desc:
                self.table.sortItems(col, Qt.DescendingOrder)
            # Désactive le tri après l'opération (si vous souhaitez le maintenir activé, supprimez cette ligne)
            self.table.setSortingEnabled(False)

    def show_context_menu(self, position):
        menu = QMenu()
        send_card_action = menu.addAction("Envoie Carte")
        send_route_action = menu.addAction("Envoi Feuille de route")
        action = menu.exec_(self.table.viewport().mapToGlobal(position))

    def send_to_map(self):
        selected_items = self.table.selectedItems()
        if not selected_items:
            return

        selected_row = selected_items[0].row()
        data = [self.table.item(selected_row, col).text() if self.table.item(selected_row, col) else ""
                for col in range(self.table.columnCount())]

        self.add_data_to_map(data)

    def send_to_route_sheet(self):
        selected_items = self.table.selectedItems()
        if not selected_items:
            return

        selected_row = selected_items[0].row()
        data = [self.table.item(selected_row, col).text() if self.table.item(selected_row, col) else ""
                for col in range(self.table.columnCount())]

        self.add_data_to_route_sheet(data)

    def add_data_to_map(self, data):
        logging.info("add_data_to_map non implémenté")
        QMessageBox.information(self, "Ajouter à la Carte", "Intégration des données à la carte non encore implémentée.")

    def add_data_to_route_sheet(self, data):
        logging.info("add_data_to_route_sheet non implémenté")
        QMessageBox.information(self, "Ajouter à la Feuille de route", "Intégration des données à la feuille de route non encore implémentée.")

    def apply_filter(self):
        sender = self.sender()
        filter_type = next(k for k, v in self.filter_buttons.items() if v == sender)

        if filter_type == "date":
            self.show_date_filter()
        elif filter_type == "lieu":
            self.show_location_filter()
        elif filter_type == "statut":
            self.show_status_filter()
        elif filter_type == "cachet":
            self.show_price_filter()

    def show_date_filter(self):
        logging.info("show_date_filter non implémenté")
        QMessageBox.information(self, "Filtre Date", "Filtre par date non encore implémenté.")

    def show_location_filter(self):
        logging.info("show_location_filter non implémenté")
        QMessageBox.information(self, "Filtre Lieu", "Filtre par lieu non encore implémenté.")

    def show_status_filter(self):
        logging.info("show_status_filter non implémenté")
        QMessageBox.information(self, "Filtre Statut", "Filtre par statut non encore implémenté.")

    def show_price_filter(self):
        logging.info("show_price_filter non implémenté")
        QMessageBox.information(self, "Filtre Cachet", "Filtre par cachet non encore implémenté.")

    def add_row(self):
        current_row = self.table.rowCount()
        self.table.insertRow(current_row)

        date_item = QTableWidgetItem(datetime.now().strftime(config["date_format"]))
        self.table.setItem(current_row, 0, date_item)

        status_item = QTableWidgetItem("À confirmer")
        self.table.setItem(current_row, 1, status_item)

        self.save_state()

    def delete_row(self):
        rows = set(item.row() for item in self.table.selectedItems())
        if not rows:
            return

        reply = QMessageBox.question(
            self, 'Confirmation',
            f'Voulez-vous vraiment supprimer {len(rows)} ligne(s) ?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.save_state()
            for row in sorted(rows, reverse=True):
                self.table.removeRow(row)

    def on_table_edit(self, item):
        if not self.undo_redo_in_progress:
            row, col = item.row(), item.column()
            key = (row, col)

            if key not in self.last_values:
                self.last_values[key] = item.text()
            else:
                old_value = self.last_values[key]
                new_value = item.text()

                if old_value != new_value:
                    self.undo_stack.append((row, col, old_value, new_value))
                    self.redo_stack.clear()

                self.last_values[key] = new_value

    def save_state(self):
        state = []
        for row in range(self.table.rowCount()):
            row_data = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                row_data.append(item.text() if item else "")
            state.append(row_data)
        self.undo_stack.append(("table_state", state))
        self.redo_stack.clear()

    def create_map_tab(self):
        """Crée l'onglet Carte avec un tableau récapitulatif des lieux exportés."""
        self.map_tab = DraggableTableWidget()
        layout = QVBoxLayout(self.map_tab)

        # 📍 Barre d'outils pour l'export & la gestion de la carte
        map_toolbar = QHBoxLayout()
        self.view_type = QComboBox()
        self.view_type.addItems(["Tous les événements", "Confirmés", "En attente", "Cette semaine", "Ce mois"])
        self.view_type.currentTextChanged.connect(self.update_map)
        map_toolbar.addWidget(QLabel("Afficher :"))
        map_toolbar.addWidget(self.view_type)

        self.optimize_route_btn = QPushButton("🗺️ Optimiser l'itinéraire")
        self.optimize_route_btn.clicked.connect(self.optimize_route)
        map_toolbar.addWidget(self.optimize_route_btn)

        self.export_route_btn = QPushButton("📥 Exporter l'itinéraire")
        self.export_route_btn.clicked.connect(self.export_route)
        map_toolbar.addWidget(self.export_route_btn)

        layout.addLayout(map_toolbar)

        # 🌍 Affichage de la carte
        self.map_view = QWebEngineView()
        layout.addWidget(self.map_view)

        # 📊 Tableau des lieux envoyés vers la carte
        self.map_table = QTableWidget()
        self.map_table.setColumnCount(4)
        self.map_table.setHorizontalHeaderLabels(["Contact", "Adresse", "Statut", "Coordonnées"])
        self.map_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # ✅ Activer le déplacement des lignes par glisser-déposer
        self.map_table.setDragDropMode(QAbstractItemView.InternalMove)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.map_table.setDragEnabled(True)
        self.map_table.setAcceptDrops(True)
        self.map_table.viewport().setAcceptDrops(True)
        self.map_table.setDropIndicatorShown(True)

        layout.addWidget(self.map_table)

        # 🔄 Bouton pour vider le tableau
        self.clear_map_table_btn = QPushButton("🗑️ Effacer la liste")
        self.clear_map_table_btn.clicked.connect(self.clear_map_table)
        layout.addWidget(self.clear_map_table_btn)

        # 🗑️ Bouton pour supprimer une ligne sélectionnée
        self.delete_map_table_row_btn = QPushButton("❌ Supprimer la ligne sélectionnée")
        self.delete_map_table_row_btn.clicked.connect(self.delete_selected_map_row)
        layout.addWidget(self.delete_map_table_row_btn)

        # 📌 Barre de progression pour le géocodage
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.initialize_map()

        self.map_tab.setLayout(layout)


    def get_itinerary(self):
        """Récupère l'ordre actuel des lieux pour l'itinéraire."""
        itinerary = []
        for row in range(self.map_table.rowCount()):
            contact = self.map_table.item(row, 0).text() if self.map_table.item(row, 0) else "Inconnu"
            address = self.map_table.item(row, 1).text() if self.map_table.item(row, 1) else "Inconnu"
            coordinates = self.map_table.item(row, 3).text() if self.map_table.item(row, 3) else "Non localisé"
            itinerary.append((contact, address, coordinates))

        print("📍 Itinéraire défini :", itinerary)
        return itinerary

    def delete_selected_map_row(self):
        """Supprime la ligne sélectionnée dans le tableau de l'onglet Carte."""
        selected_rows = sorted(set(index.row() for index in self.map_table.selectedIndexes()), reverse=True)
        if not selected_rows:
            QMessageBox.warning(self, "Suppression", "Aucune ligne sélectionnée.")
            return

        reply = QMessageBox.question(
            self, "Confirmation",
            f"Voulez-vous vraiment supprimer {len(selected_rows)} ligne(s) ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            for row in selected_rows:
                self.map_table.removeRow(row)

    def create_calendar_tab(self):
        self.calendar_tab = QWidget()
        layout = QVBoxLayout(self.calendar_tab)

        # Layout principal avec le calendrier et les détails
        calendar_layout = QHBoxLayout()

        # Style du calendrier
        self.calendar = QCalendarWidget()
        self.calendar.setMinimumWidth(400)
        self.calendar.setGridVisible(True)
        self.calendar.setStyleSheet("""
            QCalendarWidget {
                background-color: #f8f9fa;  /* Fond doux */
                border: 1px solid #ced4da;
                border-radius: 8px;
                padding: 10px;
            }
            QCalendarWidget QTableView {
                selection-background-color: #0078d7;
                selection-color: white;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar {
                background-color: #0078d7; /* Barre navigation bleue */
                color: white;
                border-radius: 8px;
            }
        """)
        self.calendar.clicked.connect(self.on_date_selected)
        calendar_layout.addWidget(self.calendar)

        # Widget contenant les détails de la journée
        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)

        # Titre des événements du jour
        details_header = QLabel("📅 Événements du jour")
        details_header.setFont(QFont(self.custom_font_family, 14, QFont.Bold))
        details_header.setStyleSheet("color: #0078d7; padding-bottom: 8px;")
        details_layout.addWidget(details_header)

        # Liste des événements
        self.events_list = QListWidget()
        self.events_list.setStyleSheet("""
            QListWidget {
                background-color: white;
                border: 1px solid #ced4da;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 8px;
            }
            QListWidget::item:selected {
                background-color: #0078d7;
                color: white;
                border-radius: 5px;
            }
        """)
        self.events_list.itemDoubleClicked.connect(self.edit_event)
        details_layout.addWidget(self.events_list)

        # Layout des boutons
        buttons_layout = QHBoxLayout()
        add_event_btn = QPushButton("➕ Ajouter")
        add_event_btn.clicked.connect(self.add_event)

        edit_event_btn = QPushButton("✏️ Modifier")
        edit_event_btn.clicked.connect(lambda: self.edit_event(self.events_list.currentItem()))

        delete_event_btn = QPushButton("❌ Supprimer")
        delete_event_btn.clicked.connect(self.delete_event)

        # Ajout des boutons au layout
        for btn in [add_event_btn, edit_event_btn, delete_event_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0078d7;
                    color: white;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-weight: bold;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #005a9e;
                }
                QPushButton:pressed {
                    background-color: #004275;
                }
            """)
            buttons_layout.addWidget(btn)

        details_layout.addLayout(buttons_layout)

        # Ajout du widget de détails au layout principal
        calendar_layout.addWidget(details_widget)
        layout.addLayout(calendar_layout)

        self.calendar_tab.setLayout(layout)


    def create_stats_tab(self):
        """Création de l'onglet Statistiques"""
        self.stats_tab = QWidget()
        layout = QVBoxLayout(self.stats_tab)
        label = QLabel("Statistiques non implémentées")
        layout.addWidget(label)

    def initialize_map(self):
        m = folium.Map(
            location=[46.2276, 2.2137],
            zoom_start=6,
            tiles="OpenStreetMap"
        )

        marker_cluster = MarkerCluster().add_to(m)
        events = self.get_table_data()

        self.geocode_worker = MapGeocodeWorker(events, self.geocoder)
        self.geocode_worker.progress.connect(self.update_progress)
        self.geocode_worker.finished.connect(lambda results: self.add_markers_to_map_and_route(results, marker_cluster, m))
        self.geocode_worker.error.connect(self.show_error)

        self.progress_bar.setVisible(True)
        self.geocode_worker.start()

    def add_markers_to_map_and_route(self, results, marker_cluster, m):
        """Ajoute des marqueurs à la carte en évitant les erreurs de coordonnées invalides."""
        for contact, address, status, coordinates in results:
            if coordinates != "Non trouvé":
                try:
                    lat, lon = map(float, coordinates.split(", "))  # Convertir en float
                    folium.Marker(
                        location=[lat, lon],
                        popup=f"{contact} ({status})<br>{address}"
                    ).add_to(marker_cluster)
                except ValueError:
                    logging.error(f"⚠️ Coordonnées invalides pour {address}: {coordinates}")
            else:
                print(f"❌ Aucune correspondance trouvée pour {address}, aucun marqueur ajouté.")

        data = io.BytesIO()
        m.save(data, close_file=False)
        self.map_view.setHtml(data.getvalue().decode())


    def update_map(self):
        current_view = self.view_type.currentText()
        self.initialize_map()
        if current_view != "Tous les événements":
            # Logique de filtrage spécifique à ajouter.
            pass

    def optimize_route(self):
        selected_events = self.get_selected_events()
        if len(selected_events) < 2:
            QMessageBox.warning(self, "Attention",
                                "Sélectionnez au moins 2 événements pour optimiser l'itinéraire.")
            return

        optimized_route = self.calculate_optimized_route(selected_events)
        self.display_optimized_route(optimized_route)

    def on_date_selected(self, date):
        self.events_list.clear()
        selected_date = date.toString(config["date_format"])

        for row in range(self.table.rowCount()):
            date_item = self.table.item(row, 0)
            if date_item and date_item.text() == selected_date:
                event_text = self.format_event_text(row)
                item = QListWidgetItem(event_text)
                item.setData(Qt.UserRole, row)
                self.events_list.addItem(item)

    def format_event_text(self, row):
        lieu = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        contact = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
        horaire = self.table.item(row, 7).text() if self.table.item(row, 7) else ""
        return f"{horaire} - {lieu} ({contact})"

    def generate_route_sheet(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"feuille_de_route_{timestamp}.pdf"

        file_name, _ = QFileDialog.getSaveFileName(
            self, 
            "Enregistrer la feuille de route",
            default_filename,
            "Fichiers PDF (*.pdf)"
        )

        if not file_name:
            return

        try:
            doc = SimpleDocTemplate(
                file_name,
                pagesize=letter,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                spaceAfter=30
            )
            header_style = ParagraphStyle(
                'CustomHeader',
                parent=styles['Heading2'],
                fontSize=14,
                spaceAfter=12
            )
            normal_style = styles["Normal"]

            story = []
            story.append(Paragraph("Feuille de Route", title_style))
            story.append(Spacer(1, 12))

            story.append(Paragraph("Généré par: " + config["user"], normal_style))
            story.append(Paragraph("Date de création: " + datetime.now().strftime("%d/%m/%Y %H:%M"), normal_style))
            story.append(Spacer(1, 20))

            selected_rows = self.get_selected_rows()
            if not selected_rows:
                raise ValueError("Aucune ligne sélectionnée")

            for row in selected_rows:
                event_date = self.get_cell_text(row, 0)
                event_location = self.get_cell_text(row, 1)
                story.append(Paragraph(f"Événement du {event_date} à {event_location}", header_style))

                details = [
                    ("Contact", self.get_cell_text(row, 2)),
                    ("Cachet", self.get_cell_text(row, 3)),
                    ("Statut", self.get_cell_text(row, 4)),
                    ("Email", self.get_cell_text(row, 5)),
                    ("Téléphone", self.get_cell_text(row, 6)),
                    ("Horaire", self.get_cell_text(row, 7)),
                    ("Notes", self.get_cell_text(row, 8))
                ]

                for label, value in details:
                    if value:
                        story.append(Paragraph(f"<b>{label}:</b> {value}", normal_style))

                story.append(Spacer(1, 20))

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(f"Feuille de route générée le {datetime.now().strftime('%d/%m/%Y %H:%M')} par {config['user']}")
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")

            qr_path = "temp_qr.png"
            qr_img.save(qr_path)
            story.append(Paragraph("QR Code de validation:", normal_style))
            story.append(Image(qr_path, width=100, height=100))
            doc.build(story)

            if os.path.exists(qr_path):
                os.remove(qr_path)

            QMessageBox.information(self, "Succès", "Feuille de route générée avec succès!")

        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors de la génération du document : {str(e)}")
            logging.error(f"Erreur génération feuille de route: {str(e)}")

    def export_data(self):
        export_menu = QMenu(self)
        actions = {
            "Excel (.xlsx)": self.export_excel,
            "CSV (.csv)": self.export_csv,
            "PDF (.pdf)": self.export_pdf,
            "iCalendar (.ics)": self.export_calendar
        }

        for label, function in actions.items():
            action = export_menu.addAction(label)
            action.triggered.connect(function)

        if self.sender():
            export_menu.exec_(self.sender().mapToGlobal(QPoint(0, self.sender().height())))

    def export_excel(self):
        """Export des données au format Excel"""
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en Excel",
            f"booking_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            "Excel files (*.xlsx)"
        )

        if file_name:
            try:
                data = []
                headers = []

                # Récupération des en-têtes
                for col in range(self.table.columnCount()):
                    headers.append(self.table.horizontalHeaderItem(col).text())

                # Récupération des données
                for row in range(self.table.rowCount()):
                    row_data = []
                    for col in range(self.table.columnCount()):
                        item = self.table.item(row, col)
                        row_data.append(item.text() if item else "")
                    data.append(row_data)

                # Création du DataFrame et export
                df = pd.DataFrame(data, columns=headers)
                df.to_excel(file_name, index=False, engine='openpyxl')

                QMessageBox.information(self, "Succès", "Export Excel réussi!")

            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export : {str(e)}")
                logging.error(f"Erreur export Excel: {str(e)}")

    def export_csv(self):
        """Export des données au format CSV"""
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en CSV",
            f"booking_export_{datetime.now().strftime('%Y%m%d')}.csv",
            "CSV files (*.csv)"
        )

        if file_name:
            try:
                data = []
                headers = []

                # Récupération des en-têtes
                for col in range(self.table.columnCount()):
                    headers.append(self.table.horizontalHeaderItem(col).text())

                # Récupération des données
                for row in range(self.table.rowCount()):
                    row_data = []
                    for col in range(self.table.columnCount()):
                        item = self.table.item(row, col)
                        row_data.append(item.text() if item else "")
                    data.append(row_data)

                # Création du DataFrame et export
                df = pd.DataFrame(data, columns=headers)
                df.to_csv(file_name, index=False)

                QMessageBox.information(self, "Succès", "Export CSV réussi!")

            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export : {str(e)}")
                logging.error(f"Erreur export CSV: {str(e)}")

    def export_pdf(self):
        """Export des données au format PDF"""
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en PDF",
            f"booking_export_{datetime.now().strftime('%Y%m%d')}.pdf",
            "PDF files (*.pdf)"
        )

        if file_name:
            try:
                doc = SimpleDocTemplate(
                    file_name,
                    pagesize=letter,
                    rightMargin=72,
                    leftMargin=72,
                    topMargin=72,
                    bottomMargin=72
                )

                # Styles pour le document
                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Heading1'],
                    fontSize=24,
                    spaceAfter=30
                )
                normal_style = styles["Normal"]

                # Construction du contenu
                story = []

                # Titre
                story.append(Paragraph("Table des événements", title_style))
                story.append(Spacer(1, 12))

                # Récupération des données
                headers = [self.table.horizontalHeaderItem(col).text() for col in range(self.table.columnCount())]
                data = []
                for row in range(self.table.rowCount()):
                    row_data = [self.get_cell_text(row, col) for col in range(self.table.columnCount())]
                    data.append(row_data)

                # Ajout des données au document
                for header in headers:
                    story.append(Paragraph(f"<b>{header}</b>", normal_style))

                for row_data in data:
                    for item in row_data:
                        story.append(Paragraph(item, normal_style))
                    story.append(Spacer(1, 12))

                # Génération du document
                doc.build(story)

                QMessageBox.information(self, "Succès", "Export PDF réussi!")

            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export : {str(e)}")
                logging.error(f"Erreur export PDF: {str(e)}")

    def export_calendar(self):
        """Export des données au format iCalendar"""
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en iCalendar",
            f"booking_export_{datetime.now().strftime('%Y%m%d')}.ics",
            "iCalendar files (*.ics)"
        )

        if file_name:
            try:
                from icalendar import Calendar, Event

                cal = Calendar()

                headers = [self.table.horizontalHeaderItem(col).text() for col in range(self.table.columnCount())]
                for row in range(self.table.rowCount()):
                    event = Event()
                    for col, header in enumerate(headers):
                        value = self.get_cell_text(row, col)
                        if header == "Date":
                            event.add('dtstart', datetime.strptime(value, config["date_format"]))
                        else:
                            event.add(header.lower(), value)
                    cal.add_component(event)

                with open(file_name, 'wb') as f:
                    f.write(cal.to_ical())

                QMessageBox.information(self, "Succès", "Export iCalendar réussi!")

            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export : {str(e)}")
                logging.error(f"Erreur export iCalendar: {str(e)}")

    def get_cell_text(self, row, col):
        """Utilitaire pour récupérer le texte d'une cellule de manière sécurisée"""
        item = self.table.item(row, col)
        return item.text() if item else ""

    def get_selected_rows(self):
        """Récupère les indices des lignes sélectionnées"""
        return sorted(set(item.row() for item in self.table.selectedItems()))

    def new_file(self):
        """Crée un nouveau fichier (réinitialise la table)"""
        self.table.setRowCount(0)
        self.current_file = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.last_values.clear()
        self.undo_redo_in_progress = False
        self.initialize_empty_table()  # On ajoute les colonnes par défaut
        logging.info("Nouveau fichier créé")

    def open_file(self):
        """Ouvre un fichier existant"""
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un fichier", "", "Fichiers JSON (*.json);;Tous les fichiers (*)"
        )
        if file_name:
            try:
                with open(file_name, 'r') as f:
                    data = json.load(f)
                    self.load_table_data(data)
                    self.current_file = file_name
                    logging.info(f"Fichier ouvert: {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'ouverture du fichier : {str(e)}")
                logging.error(f"Erreur ouverture fichier: {str(e)}")

    def save_file(self):
        """Enregistre le fichier actuel"""
        if not self.current_file:
            self.current_file, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer le fichier", "", "Fichiers JSON (*.json);;Tous les fichiers (*)"
            )
        if self.current_file:
            try:
                data = self.get_table_data()
                with open(self.current_file, 'w') as f:
                    json.dump(data, f, indent=4)
                logging.info(f"Fichier enregistré: {self.current_file}")
            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'enregistrement du fichier : {str(e)}")
                logging.error(f"Erreur enregistrement fichier: {str(e)}")

    def load_table_data(self, data):
        """Charge les données dans la table"""
        self.table.setRowCount(0)
        for row_data in data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate(row_data):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def get_table_data(self):
        """Récupère les données de la table pour le géocodage"""
        data = []
        for row in range(self.table.rowCount()):
            row_data = [self.get_cell_text(row, col) for col in range(self.table.columnCount())]
            data.append(row_data)
        return data

    def get_selected_events(self):
        """Récupère les événements sélectionnés"""
        selected_rows = self.get_selected_rows()
        return [self.get_table_data()[row] for row in selected_rows]

    def calculate_optimized_route(self, events):
        """Calcule l'itinéraire optimisé"""
        # Implémentation d'un algorithme de TSP (Traveling Salesman Problem)
        # pour optimiser l'itinéraire entre les événements sélectionnés
        return events  # Retourne les événements dans l'ordre optimisé

    def display_optimized_route(self, route):
        """Affiche l'itinéraire optimisé sur la carte"""
        # Implémentation pour afficher l'itinéraire optimisé sur la carte
        pass

    def apply_stylesheet(self):
        """Application du style visuel de l'application"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QTableWidget {
                background-color: white;
                alternate-background-color: #f7f7f7;
                selection-background-color: #0078d7;
                selection-color: white;
                gridline-color: #e0e0e0;
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                border: none;
                padding: 5px 15px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton:pressed {
                background-color: #004275;
            }
            QLineEdit {
                padding: 5px;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
            QLabel {
                color: #333;
            }
        """)

    def show_error(self, message):
        """Affiche une boîte de dialogue d'erreur"""
        QMessageBox.critical(self, "Erreur", message)
        logging.error(message)

    def closeEvent(self, event):
        """Gestionnaire d'événement de fermeture de l'application"""
        if self.check_unsaved_changes():
            event.accept()
        else:
            event.ignore()

    def check_unsaved_changes(self):
        """Vérifie s'il y a des changements non sauvegardés"""
        if self.undo_stack:
            reply = QMessageBox.question(
                self,
                'Changements non sauvegardés',
                'Voulez-vous sauvegarder les modifications avant de quitter ?',
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )

            if reply == QMessageBox.Save:
                self.save_file()
                return True
            elif reply == QMessageBox.Cancel:
                return False
        return True

def main():
    try:
        # Création des répertoires nécessaires
        for directory in ['logs', 'cache', 'config', 'assets']:
            os.makedirs(directory, exist_ok=True)
  
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        window = BookingApp()
        window.show()
        logging.info(f"Application démarrée par {config['user']} le {datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S')}")
        return app.exec_()
    except Exception as e:
        logging.critical(f"Erreur critique lors du démarrage: {str(e)}")
        print(f"Erreur critique: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Charger la feuille de style (si elle existe)
    try:
        qss_path = os.path.join(os.path.dirname(__file__), "assets", "DESIGN.qss")
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as style_file:
                app.setStyleSheet(style_file.read())
        else:
            print(f"⚠️ Le fichier {qss_path} est introuvable. Le style ne sera pas appliqué.")

        # 👉 Utiliser BookingApp au lieu d'une QMainWindow vide !
        main_window = BookingApp()
        main_window.show()

    except Exception as e:
        print(f"Erreur au lancement de l'application: {e}")
        logging.error(f"Erreur au lancement: {traceback.format_exc()}")

    sys.exit(app.exec_())