import sys
import os
os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"
import time
import logging
import json
import io
import traceback
import requests
import difflib
import qrcode
import pytz
import numpy as np
import pandas as pd
import chardet
import re 

from datetime import datetime
from typing import Any, Optional
from zipfile import BadZipFile
from pandas.errors import EmptyDataError, ParserError
from openpyxl import load_workbook
from PIL import Image
from functools import partial

# PyQt5
from PyQt5 import QtCore
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPoint, QUrl, QTimer, QPropertyAnimation
from PyQt5.QtGui import QKeySequence, QFontDatabase, QFont, QIcon, QColor
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QFileDialog, QMessageBox, QPushButton, QMenu,
    QAction, QFormLayout, QHeaderView, QLabel, QTabWidget, QToolBar,
    QShortcut, QComboBox, QLineEdit, QListWidget, QListWidgetItem,
    QProgressBar, QWidget, QCalendarWidget, QTextEdit, QProgressDialog,
    QAbstractItemView, QInputDialog, QSplitter, QGraphicsOpacityEffect, QDialog, QSizePolicy
)

# ReportLab (PDF)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

# Cartographie & Géolocalisation
import folium
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut


# Configuration
CONFIG_FILE = "config/settings.json"
GEOCODE_CACHE_FILE = "cache/geocode_cache.json"

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
    except (FileNotFoundError, json.JSONDecodeError):
        logging.error("Erreur lors du chargement du fichier de configuration. Utilisation des valeurs par défaut.")
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


class SearchThread(QThread):
    results_found = pyqtSignal(list)  # Signal émettant les résultats trouvés
    progress = pyqtSignal(int)  # Signal pour la progression

    def __init__(self, search_term, folder_path):
        super().__init__()
        self.search_term = self.normalize_text(search_term)  # Normalisation du terme recherché
        self.folder_path = folder_path

    def normalize_text(self, text):
        """Normalise le texte en minuscule et sans accents pour éviter les erreurs de recherche."""
        import unicodedata
        return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8').lower()

    def run(self):
        results = []
        files = [f for f in os.listdir(self.folder_path) if f.endswith(".csv") or f.endswith(".xlsx")]
        total_files = len(files)

        for i, file in enumerate(files):
            file_path = os.path.join(self.folder_path, file)
            try:
                if file.endswith(".csv"):
                    df = self.load_csv(file_path)
                else:
                    df = self.load_excel(file_path)

                if df is None or df.empty:
                    continue  # Ignorer les fichiers vides
                
                df.fillna("", inplace=True)  # Éviter les NaN
                
                for index, row in df.iterrows():
                    for col_name, value in row.items():
                        if self.search_term in self.normalize_text(str(value)):
                            results.append([file, index] + list(row.values))
                            break  # Évite les doublons par ligne
                
            except Exception as e:
                print(f"❌ Erreur lors du traitement de {file}: {e}")
            
            self.progress.emit(int((i + 1) / total_files * 100))  # Mise à jour de la progression
        
        self.results_found.emit(results)

    def load_csv(self, file_path):
        """Charge un fichier CSV avec gestion des erreurs d'encodage et de format."""
        encodings = ["utf-8", "latin-1", "ISO-8859-1"]
        for encoding in encodings:
            try:
                return pd.read_csv(
                    file_path, dtype=str, keep_default_na=False,
                    on_bad_lines='skip', encoding=encoding, sep=None, engine="python"
                )
            except Exception as e:
                print(f"⚠️ Erreur de chargement CSV ({encoding}) pour {file_path}: {e}")
        return None

    def load_excel(self, file_path):
        """Charge un fichier Excel avec gestion des erreurs."""
        try:
            return pd.read_excel(file_path, dtype=str, engine="openpyxl")
        except Exception as e:
            print(f"⚠️ Erreur de chargement Excel pour {file_path}: {e}")
        return None

class SearchTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder_path = "C:\\Users\\pierr\\OneDrive\\Bureau\\TABLEURS BOOKING"

        layout = QVBoxLayout()
        self.label = QLabel("Entrez votre recherche :")
        layout.addWidget(self.label)
        
        self.search_bar = QLineEdit()
        layout.addWidget(self.search_bar)
        
        self.search_button = QPushButton("🔍 Rechercher")
        self.search_button.clicked.connect(self.start_search)
        layout.addWidget(self.search_button)
        
        self.clear_button = QPushButton("🆕 Nouveau")
        self.clear_button.clicked.connect(self.clear_search)
        layout.addWidget(self.clear_button)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(6)  # Colonnes organisées
        self.results_table.setHorizontalHeaderLabels(["Fichier", "Ligne", "Nom", "Adresse", "Email", "Téléphone"])
        self.results_table.itemDoubleClicked.connect(self.open_file_at_line)  
        layout.addWidget(self.results_table)
        
        self.setLayout(layout)

    def start_search(self):
        search_term = self.search_bar.text().strip()
        if search_term:
            self.search_button.setEnabled(False)  
            self.results_table.setRowCount(0)  
            self.progress_bar.setValue(0)  
            self.thread = SearchThread(search_term, self.folder_path)
            self.thread.results_found.connect(self.display_results)
            self.thread.progress.connect(self.update_progress)
            self.thread.start()
    
    def display_results(self, results):
        if not results:
            QMessageBox.information(self, "Résultat", "Aucun résultat trouvé.")
            self.search_button.setEnabled(True)
            return
        
        self.results_table.setRowCount(len(results))
        for row_index, result in enumerate(results):
            file_name, line_number, *row_data = result  # On sépare les données
            
            structured_data = self.organize_data(row_data)  # On structure les données
            
            row_values = [file_name, line_number] + structured_data
            for col_index, data in enumerate(row_values):
                item = QTableWidgetItem(str(data))
                
                # 🔥 Mise en surbrillance du mot-clé recherché
                if self.search_bar.text().lower() in str(data).lower():
                    item.setBackground(QColor(255, 255, 100))  # Jaune clair
                
                self.results_table.setItem(row_index, col_index, item)

        self.search_button.setEnabled(True)

    def organize_data(self, row_data):
        """Analyse et classe les données extraites pour éviter le bazar dans les résultats."""
        nom = adresse = email = telephone = site_web = "—"

        for cell in row_data:
            cell = str(cell).strip()

            if re.match(r"^\+?\d[\d\s().-]{5,}$", cell):  
                telephone = cell  # 📞 C'est un numéro de téléphone
            
            elif re.match(r"^[^@]+@[^@]+\.[a-z]{2,}$", cell, re.IGNORECASE):  
                email = cell  # 📧 C'est une adresse email
            
            elif "www." in cell or cell.startswith("http"):  
                site_web = cell  # 🌐 C'est un site web
            
            elif any(keyword in cell.lower() for keyword in ["rue", "avenue", "boulevard", "place", "chemin", "route"]):
                adresse = cell  # 🏠 C'est une adresse
            
            else:
                nom = cell  # 🎤 Ça doit être un nom/contact
        
        return [nom, adresse, email, telephone]

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def clear_search(self):
        """Réinitialise la recherche, vide le tableau et réinitialise la barre de progression."""
        self.search_bar.clear()
        self.results_table.setRowCount(0)
        self.progress_bar.setValue(0)

    def open_file_at_line(self, item):
        """Ouvre le fichier Excel et positionne sur la ligne cliquée."""
        row = item.row()
        file_name = self.results_table.item(row, 0).text()
        line_number = int(self.results_table.item(row, 1).text())

        file_path = os.path.join(self.folder_path, file_name)
        if os.path.exists(file_path):
            os.system(f'start excel "{file_path}"')  # Ouvre le fichier Excel
            QMessageBox.information(self, "Info", f"Ouvrez la ligne {line_number} manuellement.")


class MapManager:
    def __init__(self, map_view, parent=None):  # ✅ Correction ici
        self.map_view = map_view
        self.parent = parent  # ✅ Assigne le parent correctement
        self.map = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="OpenStreetMap")
        self.marker_cluster = MarkerCluster().add_to(self.map)
        self.markers = {}  # Dictionnaire pour gérer les marqueurs individuellement

    def add_marker(self, name, lat, lon, category="Itinéraire"):
        """Ajoute un marqueur sur la carte."""
        print(f"📍 Ajout du marqueur : {name} [{lat}, {lon}]")  # Debug

        marker = folium.Marker(
            location=[lat, lon],
            popup=name,
            tooltip=name,
            icon=folium.Icon(color="blue", icon="info-sign")
        )

        marker.add_to(self.map)  # ✅ Utilisation correcte de `self.map`

        # 🔄 Forcer la mise à jour de la carte via `BookingApp`
        if self.parent and hasattr(self.parent, "update_map_display"):
            self.parent.update_map_display()


    def add_contact_to_table(self, contact_name, address, status, lat, lon):
        """Ajoute un contact dans le tableau de l'onglet 'Map'."""
        if not self.parent or not hasattr(self.parent, "map_table"):
            print("⚠️ Erreur : map_table n'existe pas dans BookingApp.")  # ✅ Debug
            return

        row_count = self.parent.map_table.rowCount()
        self.parent.map_table.insertRow(row_count)
        self.parent.map_table.setItem(row_count, 0, QTableWidgetItem(contact_name))
        self.parent.map_table.setItem(row_count, 1, QTableWidgetItem(address))
        self.parent.map_table.setItem(row_count, 2, QTableWidgetItem(status))
        self.parent.map_table.setItem(row_count, 3, QTableWidgetItem(f"{lat}, {lon}"))  # ✅ Ajout des coordonnées

        print(f"📋 Contact ajouté au tableau : {contact_name} - {address} [{lat}, {lon}]")  # ✅ Debug

    def get_route(self, start, end):

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


    def add_route_to_map(self, m: folium.Map, points: list) -> float:

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
            geometry, duration, distance = self.get_route(start, end)
            if geometry:
                folium.PolyLine(geometry, color="blue", weight=5, opacity=0.7).add_to(m)  # ✅ Correction
                total_duration += duration
            else:
                logging.warning(f"Impossible de récupérer l'itinéraire entre {start} et {end}")
        return total_duration / 60  # Convertir les secondes en minutes

    def remove_marker(self, contact_name):
        """Supprime un marqueur spécifique de la carte."""
        if contact_name in self.markers:
            self.marker_cluster.remove_child(self.markers[contact_name])
            del self.markers[contact_name]
            self.update_map()

    def send_selected_contacts_to_map(self, contacts):
        """Ajoute plusieurs contacts sur la carte via MapManager et les met dans le tableau."""
        print(f"✅ Contacts reçus pour la carte : {contacts}")  # ✅ Debug

        for contact_data in contacts:
            contact_name = contact_data.get("contact", "Inconnu")
            address = contact_data.get("address", "Adresse inconnue")
            status = contact_data.get("status", "Statut inconnu")

            location = self.parent.safe_geocode([address]) if self.parent else None  # ✅ Vérification ajoutée

            if location:
                lat, lon = location["lat"], location["lon"]
                print(f"📍 Ajout du marqueur : {contact_name} ({status}) [{lat}, {lon}]")  # ✅ Debug
                self.add_marker(contact_name, lat, lon, status)
                self.add_contact_to_table(contact_name, address, status, lat, lon)  # ✅ Ajout au tableau
            else:
                print(f"⚠️ Impossible de géolocaliser : {address}")  # ✅ Debug

    def toggle_marker_visibility(self, contact_name, visible):
        """Affiche ou masque un marqueur spécifique."""
        if contact_name in self.markers:
            if visible:
                self.marker_cluster.add_child(self.markers[contact_name])
            else:
                self.marker_cluster.remove_child(self.markers[contact_name])
            self.update_map()

    def filter_markers(self, status_filter):
        """Affiche uniquement les marqueurs correspondant au statut sélectionné."""
        for contact, marker in self.markers.items():
            if status_filter in contact:
                self.marker_cluster.add_child(marker)
            else:
                self.marker_cluster.remove_child(marker)
        self.update_map()

    def update_map(self):
        """Met à jour l'affichage de la carte sans la réinitialiser."""
        print("🔄 Mise à jour de la carte...")  # ✅ Debug

        # Sauvegarder la carte actuelle dans un fichier temporaire
        map_path = "cache/map.html"
        os.makedirs(os.path.dirname(map_path), exist_ok=True)

        self.map.save(map_path)  # ✅ Enregistrement simplifié

        print(f"✅ Carte enregistrée dans {map_path}")  # ✅ Debug

        # Charger la carte mise à jour dans QWebEngineView
        local_url = QUrl.fromLocalFile(os.path.abspath(map_path))
        self.map_view.load(local_url)  # ✅ Correction ici

    def send_selected_contacts_to_map(self, contacts):
        """Ajoute plusieurs contacts sur la carte via MapManager et les met dans le tableau."""
        print(f"✅ Contacts reçus pour la carte : {contacts}")  # ✅ Debug

        for contact_data in contacts:
            contact_name = contact_data.get("contact", "Inconnu")
            address = contact_data.get("address", "Adresse inconnue")
            status = contact_data.get("status", "Statut inconnu")

            location = self.parent.safe_geocode([address]) if self.parent else None  # ✅ Vérification ajoutée

            if location:
                lat, lon = location["lat"], location["lon"]
                print(f"📍 Ajout du marqueur : {contact_name} ({status}) [{lat}, {lon}]")  # ✅ Debug
                self.add_marker(contact_name, lat, lon, status)
                self.add_contact_to_table(contact_name, address, status, lat, lon)  # ✅ Ajout au tableau
            else:
                print(f"⚠️ Impossible de géolocaliser : {address}")  # ✅ Debug

        # Mettre à jour la carte une seule fois après avoir ajouté tous les marqueurs
        self.update_map()

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
        """Exécute la géolocalisation avec mise à jour de la progression."""
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

            # Met à jour la progression en pourcentage
            self.progress.emit(int((i + 1) / len(self.contacts) * 100))

        self.finished.emit(results)

class ExcelLoaderThread(QThread):
    """Thread pour charger un fichier Excel sans bloquer l'UI."""
    finished = pyqtSignal(object)  # Signal pour renvoyer le DataFrame

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        """Exécute le chargement du fichier Excel en arrière-plan."""
        try:
            df = pd.read_excel(self.file_path, engine="openpyxl")
            df.fillna("", inplace=True)  # Remplace les valeurs NaN par des chaînes vides
            self.finished.emit(df)  # Envoie le DataFrame à l'UI
        except Exception as e:
            self.finished.emit(None)
            logging.error(f"Erreur Excel : {e}")

class SortHeaderView(QHeaderView):
    """Permet le tri des colonnes avec prise en charge des QComboBox."""
    def __init__(self, orientation, table, parent=None):
        super().__init__(orientation, parent)
        self.table = table
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setDefaultAlignment(Qt.AlignCenter)

    def store_initial_order(self):
        """Stocke l'ordre initial des lignes et vérifie la présence des QComboBox avant le tri."""
        self.stored_order = []
        statut_col = self.get_statut_column_index()

        if statut_col is None:
            print("⚠️ Impossible de stocker l'ordre initial : colonne 'Statut' introuvable.")
            return

        print("🔄 Stockage de l’ordre initial...")

        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, statut_col)
            item = self.table.item(row, statut_col)

            if widget is None:
                print(f"❌ Problème : `cellWidget()` retourne `None` pour la ligne {row}, colonne {statut_col} !")
            elif isinstance(widget, QComboBox):
                value = widget.currentText().strip()
                print(f"✅ Ligne {row} : QComboBox détectée avec valeur '{value}'")
            elif item and item.text():
                value = item.text().strip()
                print(f"⚠️ Ligne {row} : Texte trouvé au lieu d'une QComboBox -> '{value}'")
            else:
                value = "Nouveau"
                print(f"❌ Ligne {row} : Aucune donnée détectée. Assignation par défaut : 'Nouveau'.")

            self.stored_order.append((value, row))

        print(f"📌 [BookingApp] Ordre initial stocké : {self.stored_order}")

    def reorder_rows(self, row_order):
        """Réorganise les lignes du tableau après un tri."""
        if not row_order:
            print("⚠️ Aucune ligne à réordonner, annulation du tri.")
            return  

        self.table.setSortingEnabled(False)  

        new_data = []
        for row in row_order:
            row_data = []
            for col in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, col)
                item = self.table.item(row, col)

                if isinstance(widget, QComboBox):
                    row_data.append(("QComboBox", widget.currentText()))  
                elif item:
                    row_data.append(("QTableWidgetItem", item.text()))
                else:
                    row_data.append(("Empty", ""))

            new_data.append(row_data)

        self.table.setRowCount(0)  
        for row_data in new_data:
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            for col, (cell_type, value) in enumerate(row_data):
                if cell_type == "QComboBox":
                    combobox = QComboBox()
                    combobox.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
                    combobox.setCurrentText(value)
                    combobox.currentIndexChanged.connect(lambda _, r=row_index, c=col, cb=combobox: self.update_status_value(r, c, cb))
                    self.table.setCellWidget(row_index, col, combobox)
                elif cell_type == "QTableWidgetItem":
                    self.table.setItem(row_index, col, QTableWidgetItem(value))

        QApplication.processEvents()  
        self.table.setSortingEnabled(True) 


    def get_statut_column_index(self):
        """Retourne l'index de la colonne 'Statut' en déléguant à BookingApp si possible."""
        parent_app = self.table.parent()
        if parent_app and hasattr(parent_app, "get_statut_column_index"):
            index = parent_app.get_statut_column_index()
            print(f"    ✅ [SortHeaderView] Délégation : Colonne 'Statut' trouvée dans BookingApp : Index {index}")
            return index
        # Sinon, parcourir les colonnes de ce header
        for i in range(self.table.columnCount()):
            header = self.table.horizontalHeaderItem(i)
            if header and header.text().strip().lower() == "statut":
                print(f"    ✅ [SortHeaderView] Colonne 'Statut' trouvée localement : Index {i}")
                return i
        print("    ⚠️ [SortHeaderView] Colonne 'Statut' introuvable !")
        return None

    def mousePressEvent(self, event):
        idx = self.logicalIndexAt(event.pos())
        if idx < 0:
            super().mousePressEvent(event)
            return

        # Vérifier qu'on clique sur la colonne "Statut"
        if idx == self.get_statut_column_index():
            menu = QMenu(self)
            action_sort = menu.addAction("Trier par statut")
            action = menu.exec_(self.mapToGlobal(event.pos()))

            if action == action_sort:
                self.sort_column(idx, Qt.AscendingOrder)
        else:
            super().mousePressEvent(event)

    def sort_column(self, column, order):
        """Trie la colonne 'Statut' après correction des erreurs et vérification des valeurs."""
        statut_col = self.get_statut_column_index()
        if statut_col is None:
            print("⚠️ Impossible de trier : colonne 'Statut' introuvable.")
            return

        data = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, statut_col)
            if isinstance(widget, QComboBox):
                value = widget.currentText().strip()  # Récupère la valeur actuelle
            else:
                value = "Nouveau"  # Valeur par défaut si problème

            # Définir un ordre de tri personnalisé
            statut_order = {
                "Nouveau": 0, "Mail envoyé": 1, "Échange Tel.": 2,
                "Full": 3, "Laisse tomber": 4, "Let's Go": 5
            }
            priority = statut_order.get(value, 99)
            data.append((priority, row))

        # Trier les lignes
        sorted_rows = sorted(data, key=lambda x: x[0], reverse=(order == Qt.DescendingOrder))
        row_order = [row for _, row in sorted_rows]

        # Appliquer le nouvel ordre des lignes
        self.reorder_rows(row_order)


    def debug_column_index(self):
        """ Vérifie si la colonne 'Statut' a bien un index cohérent. """
        statut_col = self.get_statut_column_index()
        print(f"🔍 Vérification colonne 'Statut' -> Index détecté : {statut_col}")
        if statut_col is None:
            print("❌ Aucune colonne 'Statut' trouvée. Vérifiez les noms des colonnes !")
        else:
            print(f"✅ Colonne 'Statut' trouvée : Index {statut_col}")

    def debug_stored_order(self):
        """ Vérifie l'ordre initial stocké avant tri. """
        if hasattr(self, "stored_order"):
            print(f"📌 Ordre initial stocké : {self.stored_order}")
        else:
            print("⚠️ Aucun ordre initial stocké. Vérifiez 'store_initial_order()'.")

    def check_statut_integrity(self):
        """ Vérifie et corrige les cellules de la colonne 'Statut' en ajoutant des QComboBox si elles manquent. """
        statut_col = self.get_statut_column_index()
        if statut_col is None:
            print("⚠️ Impossible de vérifier les QComboBox : colonne 'Statut' introuvable.")
            return

        print("🔍 Vérification de l’intégrité de la colonne 'Statut'...")

        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, statut_col)
            item = self.table.item(row, statut_col)

            if isinstance(widget, QComboBox):
                print(f"✅ Ligne {row} : QComboBox détectée avec valeur '{widget.currentText()}'")
            else:
                print(f"❌ Ligne {row} : Aucune QComboBox détectée ! Forçage de l'ajout...")
                self.force_combobox(row, statut_col, "Nouveau")

        self.table.update()  # 🔄 Forçage du rafraîchissement

    def force_combobox(self, row, col, value):
        """ Remplace une cellule texte par une QComboBox contenant les valeurs de statut. """
        combobox = QComboBox()
        combobox.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
        combobox.setCurrentText(value)
        combobox.currentIndexChanged.connect(lambda _, r=row, c=col, cb=combobox: self.update_status_value(r, c, cb))
        self.table.setCellWidget(row, col, combobox)
        print(f"🛠️ QComboBox forcée en ligne {row}, colonne {col}, valeur '{value}'.")

    def mouseDoubleClickEvent(self, event):
        idx = self.logicalIndexAt(event.pos())
        if idx < 0:
            return

        menu = QMenu(self)
        action_asc = menu.addAction("Trier de A à Z")
        action_desc = menu.addAction("Trier de Z à A")
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == action_asc:
            self.sort_column(idx, Qt.AscendingOrder)
        elif action == action_desc:
            self.sort_column(idx, Qt.DescendingOrder)


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
        """Gère le glisser-déposer des lignes tout en conservant les QComboBox."""
        selected_rows = sorted(set(index.row() for index in self.selectedIndexes()))
        target_index = self.indexAt(event.pos())
        target_row = target_index.row() if target_index.isValid() else self.rowCount()

        if not selected_rows:
            return

        # ✅ Stocker les données des cellules, y compris les QComboBox
        rows_data = []
        for row in selected_rows:
            row_data = []
            for col in range(self.columnCount()):
                widget = self.cellWidget(row, col)
                item = self.item(row, col)

                if isinstance(widget, QComboBox):
                    row_data.append(("QComboBox", widget.currentText()))
                elif item:
                    row_data.append(("QTableWidgetItem", item.text()))
                else:
                    row_data.append(("Empty", ""))

            rows_data.append(row_data)

        # ✅ Supprimer les lignes sélectionnées
        for row in reversed(selected_rows):
            self.removeRow(row)

        # ✅ Réinsérer les lignes déplacées avec les bonnes valeurs
        for i, row_data in enumerate(rows_data):
            self.insertRow(target_row + i)
            for col, (cell_type, value) in enumerate(row_data):
                if cell_type == "QComboBox":
                    combobox = QComboBox()
                    combobox.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
                    combobox.setCurrentText(value)
                    combobox.currentIndexChanged.connect(lambda _, r=target_row + i, c=col, cb=combobox: self.parent().update_status_value(r, c, cb))
                    self.setCellWidget(target_row + i, col, combobox)
                elif cell_type == "QTableWidgetItem":
                    self.setItem(target_row + i, col, QTableWidgetItem(value))

        event.accept()

        # ✅ Forcer un tri après le glisser-déposer
        header_view = self.parent().header_view  # 📌 Récupérer `SortHeaderView`
        statut_col = header_view.get_statut_column_index()
        if statut_col is not None:
            header_view.sort_column(statut_col, Qt.AscendingOrder)


class BookingApp(QMainWindow):
    def __init__(self):
        super().__init__()  # ✅ Appel du constructeur parent
        self.current_file = None
        self.undo_stack = []
        self.redo_stack = []
        self.last_values = {}
        self.row_colors = {}  # ✅ Correction pour éviter AttributeError

        # 📊 Initialisation du tableau
        self.table = QTableWidget(self)
        self.table.setColumnCount(4)  # ✅ Ajout de la colonne "Formule"
        self.table.setHorizontalHeaderLabels(["Date", "Statut", "Cachet", "Formule"])  # ✅ Mise à jour des en-têtes

        # 🏷️ Initialisation du tri personnalisé
        self.header_view = SortHeaderView(Qt.Horizontal, self.table)  # ✅ Stocker une référence à SortHeaderView
        self.table.setHorizontalHeader(self.header_view)  # ✅ Appliquer SortHeaderView au tableau

        # 🎛️ Ajout du menu contextuel pour le tri
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.show_header_menu)

        # 📅 Configuration des colonnes dynamiques
        self.setup_date_column()
        self.setup_status_column()
        self.setup_formule_column()  # ✅ Ajout du menu déroulant pour "Formule"

        # 🔄 Ajustement automatique des colonnes
        self.adjust_columns()

        # 🗺️ Initialisation de la carte et du gestionnaire de marqueurs
        self.map = None
        self.map_view = QWebEngineView()
        self.map_manager = MapManager(self.map_view, parent=self)  # ✅ Passage correct du parent
        self.map_manager.parent = self
        self.marker_cluster = None

        # 📌 Initialisation des fonctionnalités principales
        self.setup_autosave()
        self.setup_shortcuts()
        self.filter_invalid_fonts()

        # 🖼️ Configuration de la fenêtre principale
        self.setWindowTitle("BALOON - Créateur de Tournée")
        self.setGeometry(100, 100, 600, 400)

        # 📑 Création des onglets et affichage du tableau
        self.setupUI()
        self.create_table_tab()
        self.setCentralWidget(self.table_tab)

        # 👀 Affichage de la fenêtre principale
        self.show()

        # 📍 Liste des contacts affichés sur la carte (utilisés pour la géolocalisation et les itinéraires)
        self.map_contacts = []

        # 🔠 Chargement de la police personnalisée (si disponible)
        self.load_custom_font()

        # 🌍 Initialisation du géocodeur avec un timeout pour éviter les blocages
        self.geocoder = Nominatim(user_agent="booking_app", timeout=5)

        # 🛠️ Création de la barre d'outils et des raccourcis clavier pour une meilleure ergonomie
        self.create_toolbar()
        self.create_tabs()
        self.create_shortcuts()

        # 🎨 Application d'une feuille de style pour harmoniser l'apparence
        self.apply_stylesheet()

        # 📸 Chargement du logo de l'application (fonctionnalité à compléter)
        self.load_logo()

    def populate_table(self):
        """Remplit le tableau avec des QComboBox et QTableWidgetItem."""
        for row in range(self.table.rowCount()):
            combo_box = QComboBox()
            combo_box.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
            self.table.setCellWidget(row, 4, combo_box)
            print(f"✅ QComboBox ajoutée à la ligne {row}, colonne 4")

            item = QTableWidgetItem("Exemple")
            self.table.setItem(row, 3, item)
            print(f"✅ QTableWidgetItem ajouté à la ligne {row}, colonne 3")

    def trigger_sort(self):
        """Forcer le tri sur la colonne 'Statut' après modification d'un QComboBox."""
        statut_col = self.get_statut_column_index()
        if statut_col is not None:
            self.header_view.sort_column(statut_col, Qt.AscendingOrder)  # Relance le tri immédiatement

    def verify_table_integrity(self):
        """Vérifie que chaque cellule a bien son QComboBox ou QTableWidgetItem."""
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 4)
            item = self.table.item(row, 3)

            if isinstance(widget, QComboBox):
                print(f"🔍 Ligne {row}, colonne 4 : QComboBox détectée ✅")
            else:
                print(f"⚠️ Ligne {row}, colonne 4 : QComboBox manquante ❌")

            if isinstance(item, QTableWidgetItem):
                print(f"🔍 Ligne {row}, colonne 3 : QTableWidgetItem détecté ✅")
            else:
                print(f"⚠️ Ligne {row}, colonne 3 : QTableWidgetItem manquant ❌")

    def add_combobox_to_cell(self, row, col, value="Nouveau"):
        """Ajoute un QComboBox dans une cellule et met à jour le tri après modification."""
        combobox = QComboBox()
        combobox.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
        combobox.setCurrentText(value)
        
        # Détecte quand la valeur change et relance le tri
        combobox.currentIndexChanged.connect(lambda: self.trigger_sort())

        self.table.setCellWidget(row, col, combobox)


    def debug_column_index(self):
        """ Vérifie si la colonne 'Statut' a bien un index cohérent. """
        statut_col = self.get_statut_column_index()
        print(f"🔍 Vérification colonne 'Statut' -> Index détecté : {statut_col}")
        if statut_col is None:
            print("❌ Aucune colonne 'Statut' trouvée. Vérifiez les noms des colonnes !")
        else:
            print(f"✅ Colonne 'Statut' trouvée : Index {statut_col}")

    def debug_stored_order(self):
        """ Vérifie l'ordre initial stocké avant tri. """
        if hasattr(self, "stored_order"):
            print(f"📌 Ordre initial stocké : {self.stored_order}")
        else:
            print("⚠️ Aucun ordre initial stocké. Vérifiez 'store_initial_order()'.")

    def setupUI(self):
        """
        Configure les éléments de l'interface principale dans un widget central utilisant un layout vertical.
        Ce layout permet de structurer visuellement la fenêtre de manière claire et aérée.
        """
        central_widget = QWidget()
        layout = QVBoxLayout()

        """Configuration de l'interface principale."""
        self.map_table = QTableWidget()  # ✅ Assure que map_table est initialisé

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

        # Insertion d'un espace flexible pour aérer l'interface
        layout.addStretch()

        # Affecte le layout au widget central et définit ce widget comme central de la fenêtre
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def keyPressEvent(self, event):
        """Gestion des raccourcis clavier, notamment pour la suppression."""
        if event.key() == Qt.Key_Delete:
            self.delete_selected_rows()
        else:
            super().keyPressEvent(event)

    def setup_shortcuts(self):
        """Configure les raccourcis clavier en supprimant les conflits."""
        # Supprimer les anciens raccourcis en conflit
        for action in self.findChildren(QAction):
            if action.shortcut().toString() in ["Ctrl+Z", "Ctrl+Y"]:
                action.setShortcut(QKeySequence())  # Supprime le raccourci

        # Ajouter les nouveaux raccourcis propres
        self.shortcut_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        self.shortcut_copy.activated.connect(self.copy_selection)

        self.shortcut_paste = QShortcut(QKeySequence("Ctrl+V"), self)
        self.shortcut_paste.activated.connect(self.paste_selection)

        self.shortcut_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.shortcut_undo.activated.connect(self.undo)

        self.shortcut_redo = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.shortcut_redo.activated.connect(self.redo)


    def copy_selection(self):
        """Copie la sélection dans le presse-papiers."""
        selected_items = self.table.selectedItems()
        if not selected_items:
            return
        data = "\n".join("\t".join(self.table.item(row, col).text() if self.table.item(row, col) else ""
                                   for col in range(self.table.columnCount()))
                         for row in sorted(set(index.row() for index in self.table.selectedIndexes())))
        clipboard = QApplication.clipboard()
        clipboard.setText(data)

    def paste_selection(self):
        """Colle les données du presse-papiers dans le tableau."""
        clipboard = QApplication.clipboard().text()
        rows = clipboard.split("\n")
        start_row = self.table.currentRow()
        start_col = self.table.currentColumn()
        for i, row_data in enumerate(rows):
            cols = row_data.split("\t")
            for j, cell_data in enumerate(cols):
                item = QTableWidgetItem(cell_data)
                self.table.setItem(start_row + i, start_col + j, item)


    def setup_autosave(self):
        """Configure un auto-save toutes les 10 minutes."""
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.auto_save)
        self.autosave_timer.start(600000)  # 10 minutes = 600000 ms

    def auto_save(self):
        """Sauvegarde automatique du fichier en cours."""
        if self.current_file:
            self.save_file()
            logging.info("💾 Sauvegarde automatique effectuée.")
        else:
            logging.warning("⚠️ Aucun fichier ouvert, auto-save ignoré.")

    def filter_invalid_fonts(self):
        """Ignore toutes les polices commençant par 'FONTSPRING DEMO'."""
        database = QFontDatabase()
        invalid_fonts = [font for font in database.families() if font.startswith("FONTSPRING DEMO")]

        for font in invalid_fonts:
            font_path = f"assets/fonts/{font}.ttf"  # Chemin fictif, à adapter
            if os.path.exists(font_path):
                font_id = database.addApplicationFont(font_path)  # Charger depuis un fichier
                if font_id == -1:
                    print(f"⚠️ Échec de chargement de la police {font_path}")
                else:
                    database.removeApplicationFont(font_id)  # Supprimer si non utilisée


    def show_loading_screen(self, message="Chargement..."):
        """Affiche un écran semi-transparent de chargement."""
        self.loading_overlay = QWidget(self)
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 120);")

        layout = QVBoxLayout(self.loading_overlay)

        spinner_label = QLabel("⏳")
        spinner_label.setStyleSheet("font-size: 32px; color: white;")
        layout.addWidget(spinner_label, alignment=Qt.AlignCenter)

        text_label = QLabel(message)
        text_label.setStyleSheet("font-size: 18px; color: white;")
        layout.addWidget(text_label, alignment=Qt.AlignCenter)

        self.loading_overlay.show()

    def hide_loading_screen(self):
        """Cache l'écran de chargement."""
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.hide()
            self.loading_overlay.deleteLater()

    def filter_table(self):
        """Filtre les lignes du tableau en fonction du texte saisi."""
        search_text = self.search_bar.text().lower()
        for row in range(self.table.rowCount()):
            row_match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and search_text in item.text().lower():
                    row_match = True
                    break
            self.table.setRowHidden(row, not row_match)

    def load_excel_into_table(self, df):
        """Charge un DataFrame Excel dans le QTableWidget et place les colonnes fixes à droite après import."""

        # 🔎 Vérifier si le DataFrame est vide
        if df is None or df.empty:
            QMessageBox.critical(self, "Erreur", "Le fichier Excel est vide ou corrompu.")
            return

        # 📌 Colonnes fixes qui doivent être placées à l'extrême droite
        fixed_columns = ["Date", "Statut", "Cachet", "Formule"]

        # 🔎 Séparer les colonnes importées et les colonnes fixes
        imported_columns = [col for col in df.columns if col not in fixed_columns]

        # 🔄 Réorganisation : d'abord les colonnes importées, puis les colonnes fixes
        all_columns = imported_columns + fixed_columns

        # 🎯 Nettoyage et conversion des données
        df.fillna("", inplace=True)  # ✅ Remplace les NaN par des chaînes vides

        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if "Cachet" in df.columns:
            df["Cachet"] = pd.to_numeric(df["Cachet"], errors="coerce").fillna(0).astype(float)

        # 📋 Mise à jour du tableau Qt
        self.table.setRowCount(0)  # 🔄 Réinitialiser le tableau
        self.table.setColumnCount(len(all_columns))
        self.table.setHorizontalHeaderLabels(all_columns)

        self.table.setUpdatesEnabled(False)  # ✅ Désactiver les mises à jour pour accélérer l'import

        try:
            for row_index, row in df.iterrows():
                self.table.insertRow(row_index)

                # ✅ Insérer d'abord les colonnes importées
                for col_index, col_name in enumerate(imported_columns):
                    value = str(row.get(col_name, "")).strip()
                    self.table.setItem(row_index, col_index, QTableWidgetItem(value))

                # ✅ Ajouter les colonnes fixes **à droite**
                fixed_col_index = len(imported_columns)

                # ✅ Ajouter le bouton "+" pour ouvrir le calendrier dans la colonne "Date"
                self.add_date_button(row_index)

                # ✅ Ajouter la liste déroulante pour "Statut"
                self.add_status_combobox(row_index)

                # ✅ Insérer la valeur de "Cachet"
                cachet_item = QTableWidgetItem(str(row.get("Cachet", "")))
                self.table.setItem(row_index, fixed_col_index + 2, cachet_item)

                # ✅ Ajouter la liste déroulante pour "Formule"
                self.add_formule_combobox(row_index)

        finally:
            self.table.setUpdatesEnabled(True)  # ✅ Réactiver les mises à jour après l'import

        # 📏 Ajuster la largeur des colonnes
        self.adjust_column_sizes()

        # ✅ Vérification finale des données insérées
        print("✅ Données insérées dans le tableau PyQt")
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)

        self.statusBar().showMessage("✅ Import terminé avec succès.")

    def normalize_column_values(self, column_name):
        """Nettoie et normalise les valeurs d'une colonne donnée."""
        col_index = self.get_column_index_by_name(column_name)
        if col_index is None:
            return

        for row in range(self.rowCount()):
            item = self.item(row, col_index)
            if item:
                text = item.text().strip().lower()  # 🔹 Suppression des espaces et normalisation en minuscule
                if text in ["", "non précisé", "none"]:
                    item.setText("Non précisé")  # 🔹 Remplace les valeurs incohérentes


    def adjust_column_sizes(self):
        """Ajuste la largeur des colonnes après importation en gardant les colonnes fixes à droite."""
        header = self.table.horizontalHeader()

        # 📏 Ajuster la largeur des colonnes fixes et empêcher leur redimensionnement
        fixed_columns = ["Date", "Statut", "Cachet", "Formule"]
        for col in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(col)
            if header_item and header_item.text() in fixed_columns:
                header.setSectionResizeMode(col, QHeaderView.Fixed)
                self.table.resizeColumnToContents(col)  # Adapter à la taille du texte
            else:
                header.setSectionResizeMode(col, QHeaderView.Stretch)  # Remplir l'écran

    def get_statut_column_index(self):
        """Retourne l'index de la colonne 'Statut'."""
        for col in range(self.table.columnCount()):
            header = self.table.horizontalHeaderItem(col)
            if header:
                print(f"🔎 [BookingApp] Colonne {col} → {header.text().strip()}")  # ✅ Vérification
            if header and header.text().strip() == "Statut":
                print(f"✅ [BookingApp] Colonne 'Statut' trouvée : Index {col}")
                return col
        print("⚠️ [BookingApp] Colonne 'Statut' introuvable !")
        return None


    def send_selected_contacts_to_map(self):
        """Récupère les contacts sélectionnés et les envoie à MapManager."""
        print("✅ Fonction send_selected_contacts_to_map appelée")  # ✅ Debug
        selected_contacts = []

        for row in sorted(set(index.row() for index in self.table.selectedIndexes())):
            selected_contacts.append({
                "contact": self.table.item(row, 0).text() if self.table.item(row, 0) else "Inconnu",
                "address": self.table.item(row, 1).text() if self.table.item(row, 1) else "Adresse inconnue",
                "status": self.table.item(row, 2).text() if self.table.item(row, 2) else "Statut inconnu"
            })

        print(f"📌 Contacts sélectionnés : {selected_contacts}")  # ✅ Debug
        self.map_manager.send_selected_contacts_to_map(selected_contacts)  # ✅ Correction ici

    def save_action(self):
        """
        Exemple de fonction exécutée lors du clic sur "Enregistrer les modifications".
        Affiche un message de confirmation pour l'utilisateur.
        """
        QMessageBox.information(self, "Enregistrer", "Les modifications ont bien été enregistrées.")

    def setup_buttons(self):
        button_style = """
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
        """
        self.enregistrer_btn.setStyleSheet(button_style)
        self.annuler_btn.setStyleSheet(button_style.replace("#4CAF50", "#f44336").replace("#45a049", "#da190b"))

    def create_toolbar(self):
        """
        Crée une barre d'outils avec des actions pour l'application.
        """
        toolbar = QToolBar("Outils", self)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        # Ajout d'une action pour ouvrir un fichier
        open_action = QAction(QIcon("assets/open.png"), "Ouvrir", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        toolbar.addAction(open_action)

        # Ajout d'une action pour enregistrer un fichier
        save_action = QAction(QIcon("assets/save.png"), "Enregistrer", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_action)
        toolbar.addAction(save_action)

        # Ajout d'une action pour annuler la dernière opération
        undo_action = QAction(QIcon("assets/undo.png"), "Annuler", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.undo)
        toolbar.addAction(undo_action)

        # Ajout d'une action pour rétablir la dernière opération annulée
        redo_action = QAction(QIcon("assets/redo.png"), "Rétablir", self)
        redo_action.setShortcut("Ctrl+Y")
        redo_action.triggered.connect(lambda: self.redo())
        toolbar.addAction(redo_action)

        # Ajout d'une action pour créer un nouveau fichier
        new_action = QAction(QIcon("assets/new.png"), "Nouveau", self)
        new_action.triggered.connect(self.new_file)
        toolbar.addAction(new_action)

        # Ajout d'une action pour importer un fichier
        import_action = QAction(QIcon("assets/import.png"), "Importer", self)
        import_action.triggered.connect(self.import_file)
        toolbar.addAction(import_action)

        # Ajout d'une action pour exporter un fichier
        export_action = QAction(QIcon("assets/export.png"), "Exporter", self)
        export_menu = QMenu()
        export_menu.addAction("PDF", self.export_pdf)
        export_menu.addAction("Excel", self.export_excel)
        export_action.setMenu(export_menu)
        toolbar.addAction(export_action)


    def cancel_operation(self):
        """Action d'annulation générique."""
        print("Action annulée !")


    def open_file(self):
        """
        Ouvre un fichier et charge son contenu dans l'application.
        """
        options = QFileDialog.Options()
        options |= QFileDialog.ReadOnly
        file_name, _ = QFileDialog.getOpenFileName(self, "Ouvrir un fichier", "", "Tous les fichiers (*);;Fichiers CSV (*.csv);;Fichiers Excel (*.xlsx)", options=options)
        if file_name:
            try:
                if file_name.endswith('.csv'):
                    self.load_csv(file_name)
                elif file_name.endswith('.xlsx'):
                    self.load_excel(file_name)
                else:
                    QMessageBox.warning(self, "Format de fichier non supporté", "Le format de fichier sélectionné n'est pas supporté.")
            except Exception as e:
                logging.error(f"Erreur lors de l'ouverture du fichier: {e}")
                QMessageBox.critical(self, "Erreur", f"Une erreur est survenue lors de l'ouverture du fichier:\n{e}")

    def undo(self):
        """
        Annule la dernière opération effectuée.
        """
        if not self.undo_stack:
            QMessageBox.information(self, "Annuler", "Aucune opération à annuler.")
            return
        
        self.undo_redo_in_progress = True
        last_action = self.undo_stack.pop()
        self.redo_stack.append(last_action)

        if last_action["type"] == "delete":
            for row, row_data in reversed(last_action["data"]):  # Restaurer les lignes dans l'ordre
                self.table.insertRow(row)
                for col, value in enumerate(row_data):
                    if col == self.get_date_column_index():  # 📅 Colonne "Date"
                        self.add_date_button(row)  # ✅ Utilise ta fonction existante
                    elif col == self.get_statut_column_index():  # 📌 Colonne "Statut"
                        self.add_status_combobox(row)  # ✅ Utilise ta fonction existante
                    else:
                        item = QTableWidgetItem(value)
                        self.table.setItem(row, col, item)

        elif last_action["type"] == "edit":
            self.restore_last_values(last_action)

        self.undo_redo_in_progress = False


    def redo(self):
        """Rétablit la dernière action annulée."""
        if not self.redo_stack:
            QMessageBox.information(self, "Rétablir", "Aucune opération à rétablir.")
            return
        
        self.undo_redo_in_progress = True
        last_action = self.redo_stack.pop()
        self.undo_stack.append(last_action)

        if last_action["type"] == "delete":
            for row, row_data in reversed(last_action["data"]):  # Restaurer les lignes dans l'ordre
                self.table.insertRow(row)
                for col, value in enumerate(row_data):
                    if col == self.get_date_column_index():  # 📅 Colonne "Date"
                        self.add_date_button(row)  # ✅ Restaurer le bouton "+"
                    elif col == self.get_statut_column_index():  # 📌 Colonne "Statut"
                        self.add_status_combobox(row)  # ✅ Restaurer le menu déroulant
                    else:
                        item = QTableWidgetItem(value)
                        self.table.setItem(row, col, item)

        elif last_action["type"] == "edit":
            row, col, old_value, new_value = last_action["data"]
            self.undo_stack.append({"type": "edit", "data": (row, col, new_value, old_value)})
            self.table.item(row, col).setText(new_value)

        self.undo_redo_in_progress = False

    def delete_selected_rows(self):
        """Supprime les lignes sélectionnées et les enregistre pour annulation."""
        selected_rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not selected_rows:
            QMessageBox.warning(self, "Suppression", "Aucune ligne sélectionnée.")
            return

        # Stocker les lignes supprimées avant de les retirer
        deleted_data = []
        for row in selected_rows:
            row_data = [self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())]
            deleted_data.append((row, row_data))

        # Ajouter à la pile d'annulation
        self.undo_stack.append({"type": "delete", "data": deleted_data})
        self.redo_stack.clear()  # On vide la pile de rétablissement

        # Suppression effective des lignes
        for row in selected_rows:
            self.table.removeRow(row)


    def update_map_display(self):
        """Met à jour l'affichage de la carte après ajout des marqueurs."""
        map_path = "map.html"

        # ✅ Sauvegarde de la carte avant de l'afficher
        self.map_manager.map.save(map_path)

        # ✅ Vérification que le fichier `map.html` existe bien
        if not os.path.exists(map_path):
            print("❌ ERREUR : Le fichier map.html n'a pas été trouvé !")
            return  # On stoppe ici pour éviter une erreur

        # ✅ Affichage de la carte si le fichier existe
        self.map_view.setUrl(QUrl.fromLocalFile(os.path.abspath(map_path)))
        print("🌍 Carte mise à jour avec les nouveaux marqueurs !")

    def save_map_cache(self):
        """Sauvegarde la carte en cache pour consultation hors-ligne."""
        with open("cache/map_offline.html", "w", encoding="utf-8") as f:
            f.write(self.map_view.page().toHtml())

    def load_map_cache(self):
        """Charge la carte en cache si aucune connexion Internet."""
        if os.path.exists("cache/map_offline.html"):
            with open("cache/map_offline.html", "r", encoding="utf-8") as f:
                self.map_view.setHtml(f.read())


    def apply_stylesheet(self):
        try:
            qss_path = r"C:\booking_app\assets\DESIGN.qss"  # 🔥 Chemin absolu défini ici
            if os.path.exists(qss_path):
                with open(qss_path, "r", encoding="utf-8") as style_file:
                    qss_code = style_file.read()
                    self.setStyleSheet(qss_code)
                    print("✅ QSS activé avec succès !")
            else:
                print("🚨 Fichier QSS introuvable à :", qss_path)
                self.apply_default_stylesheet()
        except Exception as e:
            print(f"❌ Erreur lors du chargement du QSS : {e}")
            self.apply_default_stylesheet()

    def apply_default_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
                color: black;  /* ✅ Forcer le texte en noir */
            }
            QTableWidget {
                background-color: white;
                alternate-background-color: #f7f7f7;
                selection-background-color: #0078d7;
                selection-color: white;
                gridline-color: #e0e0e0;
                color: black;  /* ✅ Texte toujours noir */
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                border-radius: 5px;
                padding: 5px 10px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton:pressed {
                background-color: #004275;
            }
            QLabel {
                color: black;  /* ✅ Assurer la lisibilité */
            }
            QLineEdit {
                background-color: white;
                color: black;  /* ✅ Texte toujours visible */
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
            QListWidget {
                background-color: white;
                color: black;  /* ✅ Texte noir */
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
            }
        """)


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

    def safe_geocode(self, queries, retries=3, delay=2):
        """Géolocalise une adresse avec gestion des erreurs."""
        if not queries:
            print("⚠️ Aucune adresse fournie pour la géolocalisation")  # ✅ Debug
            return None

        for query in queries:
            print(f"🌍 Tentative de géolocalisation : {query}")  # ✅ Debug
            if query in geocode_cache:
                print(f"✅ Utilisation du cache pour {query}")  # ✅ Debug
                return geocode_cache[query]

            for attempt in range(retries):
                try:
                    location = self.geocoder.geocode(query, exactly_one=True, timeout=10)
                    if location:
                        result = {"lat": location.latitude, "lon": location.longitude}
                        geocode_cache[query] = result
                        print(f"📍 Coordonnées trouvées : {result}")  # ✅ Debug
                        return result
                except Exception as e:
                    print(f"❌ Erreur de géolocalisation pour {query} : {e}")  # ✅ Debug

        print("⚠️ Aucun résultat pour cette adresse")
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


    def get_displayed_contacts(self):
        """Récupère la liste des contacts affichés sur la carte."""
        contacts = []
        for row in range(self.map_table.rowCount()):
            contact = self.map_table.item(row, 0).text()
            coordinates = self.map_table.item(row, 3).text()

            if coordinates and coordinates != "Non trouvé":
                lat, lon = map(float, coordinates.split(", "))
                contacts.append((contact, lat, lon))

        return contacts


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
        font_path = os.path.join(os.path.dirname(__file__), "assets", "InterDisplay-Light.ttf")

        # Vérification de l'existence du fichier
        if not os.path.exists(font_path):
            print(f"    ❌ Erreur : fichier de police introuvable → {font_path}")
            return  # On arrête ici si le fichier n'existe pas

        font_id = QFontDatabase.addApplicationFont(font_path)

        print(f"    font_id = {font_id} (type: {type(font_id)})")  # Debugging

        if font_id == -1:
            logging.warning(f"    ⚠️ Erreur lors du chargement de la police : {font_path}")
            self.setFont(QFont("Arial", 10))  # Utilisation d'une police de secours
        else:
            font_families = QFontDatabase.applicationFontFamilies(font_id)
            if font_families:
                self.custom_font_family = font_families[0]
                self.setFont(QFont(self.custom_font_family, 10))
                print(f"    ✅ Police appliquée : {self.custom_font_family}")
            else:
                logging.warning("    ⚠️ Aucune famille de police trouvée pour l'ID chargé.")
                self.setFont(QFont("Arial", 10))  # Police de secours

    def initialize_empty_table(self):
        """
        Initialise un tableur vide avec les colonnes par défaut : "Date", "Statut" et "Cachet".
        """
        default_headers = ["Date", "Statut", "Cachet"]
        self.table.setRowCount(0)  # On vide la table
        self.table.setColumnCount(len(default_headers))
        self.table.setHorizontalHeaderLabels(default_headers)

    def adjust_columns(self):
        """Ajuste automatiquement la largeur des colonnes avec une gestion intelligente du redimensionnement."""
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        
        for col in range(self.table.columnCount()):
            if header.sectionSize(col) > 300:
                header.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(col, QHeaderView.Interactive)  # Permet le redimensionnement manuel


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
        return [self.table.horizontalHeaderItem(col).text() if self.table.horizontalHeaderItem(col) else f"Colonne {col}"
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
        """Demande à l'utilisateur d'exporter la feuille de route en PDF uniquement."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la feuille de route",
            "",
            "Fichiers PDF (*.pdf)"
        )

        if file_path:
            itinerary = self.get_itinerary()
            points = [self.get_coordinates(row[2]) for row in itinerary if row[2] != "Non localisé"]
            if len(points) < 2:
                QMessageBox.warning(self, "Export PDF", "Aucun itinéraire valide à exporter.")
                return

            route_details = self.calculate_route_details(points)
            self.export_route_to_pdf(file_path, route_details)

    def get_coordinates(self, coord_string):
        """
        Convertit une chaîne de coordonnées 'lat, lon' en tuple (lat, lon).
        Ex : "48.8566, 2.3522" → (48.8566, 2.3522)
        """
        try:
            lat, lon = map(float, coord_string.split(", "))
            return lat, lon
        except ValueError:
            print(f"⚠️ Erreur : Impossible de convertir {coord_string} en coordonnées.")
            return None

    def export_route_to_pdf(self, file_path, route_details):
        """Exporte l'itinéraire en PDF avec détails et coût du carburant."""
        try:
            pdf = SimpleDocTemplate(file_path, pagesize=letter)
            elements = []
            styles = getSampleStyleSheet()

            # 🔹 Titre du document
            title = Paragraph("<b>Feuille de route de la tournée</b>", styles["Title"])
            elements.append(title)
            elements.append(Spacer(1, 12))

            # 🛣️ Tableau des étapes du trajet
            data = [["Départ", "Arrivée", "Durée (min)", "Distance (km)"]]
            for step in route_details[:-1]:  # Dernier élément = coût carburant
                data.append([step["from"], step["to"], step["duration"], step["distance"]])

            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]
            itinerary_table = Table(data, style=table_style)
            elements.append(itinerary_table)

            elements.append(Spacer(1, 12))

            # ⛽ Coût du carburant
            cost_petrol = route_details[-1]["cost_petrol"]
            cost_diesel = route_details[-1]["cost_diesel"]
            fuel_info = Paragraph(
                f"⛽ <b>Coût estimé :</b> Essence : {cost_petrol} € | Diesel : {cost_diesel} €",
                styles["Normal"]
            )
            elements.append(fuel_info)

            # 💾 Génération du PDF
            pdf.build(elements)
            QMessageBox.information(self, "Export PDF", f"Feuille de route exportée avec succès : {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Erreur PDF", f"Une erreur est survenue : {str(e)}")


    def update_progress(self, value):
        """Met à jour la barre de progression et l'affiche si nécessaire."""
        self.progress_bar.setValue(value)
        self.progress_bar.setVisible(True if value < 100 else False)


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
        """Importe un fichier Excel et charge son contenu dans le tableau BookingApp."""
        try:
            # ✅ Vérification de l'existence du fichier
            if not os.path.exists(file_path):
                QMessageBox.critical(self, "Erreur", "Le fichier sélectionné est introuvable.")
                return

            self.statusBar().showMessage("📂 Chargement du fichier...")

            # ✅ Lancer l'importation dans un thread pour éviter de bloquer l'interface
            self.excel_thread = ExcelLoaderThread(file_path)
            self.excel_thread.finished.connect(self.load_excel_into_table)
            self.excel_thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Une erreur est survenue lors de l'importation : {str(e)}")
            logging.error(f"Erreur d'import Excel : {traceback.format_exc()}")


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
        """Importe un fichier CSV, applique un traitement des données et ajuste les colonnes après chargement."""
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

            # Construire les en-têtes : colonnes par défaut suivies des colonnes importées
            default_headers = ["Date", "Statut", "Cachet", "Formule"]
            imported_headers = [col for col in df.columns if col not in default_headers]
            all_headers = default_headers + imported_headers
            self.table.setRowCount(0)
            self.table.setColumnCount(len(all_headers))
            self.table.setHorizontalHeaderLabels(all_headers)

            # Désactiver temporairement la mise à jour pour accélérer l'import
            self.table.setUpdatesEnabled(False)
            try:
                for _, row in df.iterrows():
                    row_position = self.table.rowCount()
                    self.table.insertRow(row_position)

                    # Ajouter la date
                    date_item = QTableWidgetItem(str(row.get("Date", "")))
                    self.table.setItem(row_position, 0, date_item)

                    # Ajouter la liste déroulante pour le statut
                    self.add_status_combobox(row_position)
                    self.add_formule_combobox(row_position)

                    # Ajouter la valeur du cachet
                    cachet_item = QTableWidgetItem(str(row.get("Cachet", "")))
                    self.table.setItem(row_position, 2, cachet_item)

                    # Ajouter les autres colonnes importées
                    for col_index, col_name in enumerate(imported_headers):
                        value = row.get(col_name, "")
                        item = QTableWidgetItem(str(value).strip() if pd.notnull(value) else "")
                        self.table.setItem(row_position, len(default_headers) + col_index, item)
            finally:
                self.table.setUpdatesEnabled(True)

            # Ajuster les colonnes après l'importation
            self.adjust_columns()

            self.statusBar().showMessage(f"Fichier importé : {os.path.basename(file_path)}", 5000)
            logging.info(f"Import CSV réussi : {len(df)} lignes")
        except Exception as e:
            QMessageBox.critical(self, "Erreur technique",
                                 f"Erreur lors de l'import CSV :\n{str(e)}")
            logging.error(f"Erreur import CSV: {traceback.format_exc()}")


    def initialize_map_with_contacts(self, contacts):
        """Ajoute plusieurs contacts sur la carte et trace un itinéraire entre eux."""
        if not contacts:
            QMessageBox.warning(self, "Carte", "Aucun contact à afficher.")
            return

        m = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="OpenStreetMap")
        marker_cluster = MarkerCluster().add_to(m)

        points = []

        for contact in contacts:
            name = contact["contact"]
            address = contact["address"]
            status = contact["status"]

            location = self.safe_geocode([address])

            if location:
                lat, lon = location["lat"], location["lon"]
                points.append([lat, lon])

                folium.Marker(
                    location=[lat, lon],
                    popup=f"{name} ({status})<br>{address}",
                    icon=folium.Icon(color="blue", icon="info-sign")
                ).add_to(marker_cluster)
            else:
                print(f"⚠️ Échec : Aucune correspondance trouvée pour {address}")

        # Tracer un itinéraire entre les points
        if len(points) > 1:
            folium.PolyLine(points, color="red", weight=5, opacity=0.7).add_to(m)

        # Affichage sur l'interface
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
        """Démarre la géolocalisation sans effacer les contacts déjà affichés."""
        self.show_loading_screen("Géolocalisation en cours...")
        self.geocode_worker = MapGeocodeWorker(self.contacts, self.geocoder, self)
        self.geocode_worker.progress.connect(self.update_progress)
        self.geocode_worker.finished.connect(self.add_markers_to_map_and_table)  # ⚠️ Bien vérifier cette ligne
        self.geocode_worker.start()
        self.progress_bar.setVisible(True)
        self.hide_loading_screen()


    def debug_table_headers(self):
        """Affiche les noms des colonnes pour s'assurer qu'on récupère les bonnes données."""
        headers = [self.table.horizontalHeaderItem(i).text() if self.table.horizontalHeaderItem(i) else f"Colonne {i}"
                   for i in range(self.table.columnCount())]
        print(f"🔍 En-têtes détectés: {headers}")
        return headers


    def create_tabs(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.create_table_tab()
        self.create_map_tab()
        self.create_calendar_tab()
        self.create_stats_tab()  # Added stats tab

        self.tabs.addTab(self.table_tab, "LIEUX")
        self.tabs.addTab(self.map_tab, "CARTE")
        self.tabs.addTab(self.calendar_tab, "AGENDA")
        self.search_tab = SearchTab(self)  # Création du nouvel onglet
        self.tabs.addTab(self.search_tab, "DEMANDER A L'UNIVERS")  # Ajout dans les onglets

    def create_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save_file)
        QShortcut(QKeySequence("Ctrl+O"), self, self.open_file)
        QShortcut(QKeySequence("Ctrl+N"), self, self.new_file)
        QShortcut(QKeySequence("Ctrl+I"), self, self.import_file)
        QShortcut(QKeySequence("Ctrl+P"), self, self.export_route)
        QShortcut(QKeySequence("Ctrl+T"), self, lambda: self.tabs.setCurrentWidget(self.map_tab))
        QShortcut(QKeySequence("Ctrl+Shift+N"), self, self.insert_empty_row)

    def load_logo(self):
        logo_path = config.get("logo_path", "assets/logo.png")
        if os.path.exists(logo_path):
            self.logo = logo_path
        else:
            logging.warning(f"Logo non trouvé: {logo_path}")
            self.logo = None


    def create_table_tab(self):
        """Crée l'onglet Table avec recherche, tri et colonnes personnalisées."""
        self.table_tab = QWidget()
        layout = QVBoxLayout()

        # 🔍 Barre de recherche et tri
        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Rechercher...")
        self.search_bar.textChanged.connect(self.filter_table)
        search_layout.addWidget(self.search_bar)

        self.sort_dropdown = QComboBox()
        self.sort_dropdown.addItems([
            "Trier par...", "Date (A-Z)", "Date (Z-A)",
            "Statut (A-Z)", "Statut (Z-A)", "Cachet (A-Z)", "Cachet (Z-A)",
            "Formule (A-Z)", "Formule (Z-A)"  # ✅ Ajout du tri pour la colonne "Formule"
        ])
        self.sort_dropdown.currentIndexChanged.connect(self.handle_sort_selection)  # ✅ Correction !

        search_layout.addWidget(self.sort_dropdown)

        layout.addLayout(search_layout)

        # 🖍️ Table des contacts
        self.table = QTableWidget()
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)

        # ✅ Définition de la hauteur des lignes pour éviter le débordement des boutons
        self.table.verticalHeader().setDefaultSectionSize(30)

        # ✅ Mise à jour des colonnes
        default_headers = ["Date", "Statut", "Cachet", "Formule"]
        self.table.setColumnCount(len(default_headers))
        self.table.setHorizontalHeaderLabels(default_headers)
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table)

        # 🟢 Ajoute les boutons "+" dans la colonne "Date"
        self.setup_date_column()

        # 🔹 Ajoute la liste déroulante dans la colonne "Statut"
        self.setup_status_column()

        # 🎵 Ajoute la liste déroulante dans la colonne "Formule"
        self.setup_formule_column()

        # 🔄 Ajuste les colonnes après la configuration
        self.adjust_columns()

        # 🎯 Boutons d'action
        action_layout = QHBoxLayout()
        send_to_map_btn = QPushButton("🗺️ Envoyer vers Carte")
        send_to_map_btn.clicked.connect(self.send_selected_contacts_to_map)
        action_layout.addWidget(send_to_map_btn)

        buttons = {
            "add_row": ("➕ Ajouter", self.add_row),
            "delete_row": ("❌ Supprimer", self.delete_row),
            "generate_route": ("📝 Feuille de route", self.generate_route_sheet),
            "export": ("💾 Exporter", self.export_data)
        }
        for text, func in buttons.values():
            btn = QPushButton(text)
            btn.clicked.connect(func)
            action_layout.addWidget(btn)

        layout.addLayout(action_layout)

        self.table_tab.setLayout(layout)


    def get_date_column_index(self):
        """Retourne l'index réel de la colonne 'Date' après l'importation du fichier Excel."""
        if self.table.columnCount() == 0:  # Vérifie si le tableau est vide
            print("⚠️ Aucune colonne trouvée dans le tableau !")
            return None

        for col in range(self.table.columnCount()):  # Parcourt toutes les colonnes
            header_item = self.table.horizontalHeaderItem(col)  # Récupère le nom de colonne
            if header_item and header_item.text().strip().lower() == "date":
                return col  # Retourne l'index dès qu'on trouve "Date"

        print("⚠️ Erreur : Colonne 'Date' introuvable !")  # Debug si absent
        return None  # Retourne None si "Date" n'existe pas

    def store_row_colors(self):
        """Stocke les couleurs actuelles des lignes avant un tri."""
        self.row_colors = {}

        for row in range(self.table.rowCount()):
            color_data = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    color = item.background().color() if item.background() else QColor(Qt.white)
                    color_data.append((col, color))
            self.row_colors[row] = color_data


    def restore_row_colors(self):
        """Restaure les couleurs des lignes après un tri."""
        for row in range(self.table.rowCount()):
            color = QColor(240, 240, 240) if row % 2 == 0 else QColor(255, 255, 255)
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(color)


    def handle_sort_selection(self):
        """Gère la sélection du tri depuis le menu déroulant et appelle la bonne méthode de tri."""
        index = self.sort_dropdown.currentIndex()
        order = Qt.AscendingOrder if index % 2 == 1 else Qt.DescendingOrder  # Détermine ordre croissant/décroissant
    
        # Associe l'index du menu déroulant aux colonnes correspondantes
        column_mapping = {
            1: 0,  # Date (A-Z)
            2: 0,  # Date (Z-A)
            3: self.get_statut_column_index(),  # Statut (A-Z)
            4: self.get_statut_column_index(),  # Statut (Z-A)
            5: self.get_cachet_column_index(),  # Cachet (A-Z)
            6: self.get_cachet_column_index(),  # Cachet (Z-A)
            7: self.get_formule_column_index(),  # Formule (A-Z)
            8: self.get_formule_column_index(),  # Formule (Z-A)
        }
    
        column = column_mapping.get(index, None)
        if column is not None:
            self.table.horizontalHeader().sort_column(column, order)  # ✅ Appelle `sort_column` depuis `SortHeaderView`


    def update_status_value(self, row, col, combo_box):
        """Met à jour le statut sans déclencher de tri automatique et garantit la synchronisation."""
        if hasattr(self, "prevent_sorting") and self.prevent_sorting:
            return  

        self.prevent_sorting = True  
        selected_value = combo_box.currentText().strip()

        item = self.table.item(row, col)
        if not item:
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)

        item.setText(selected_value)  

        print(f"🔄 Statut modifié (Ligne {row}) → {selected_value}")

        if hasattr(self, "restore_row_colors") and callable(self.restore_row_colors):
            self.restore_row_colors()

        QApplication.processEvents()  

        # Ajout d'un délai pour laisser le temps à la table de se stabiliser
        QTimer.singleShot(100, lambda: self.header_view.sort_column(col, Qt.AscendingOrder))

        self.prevent_sorting = False  
  

    def setup_status_column(self):
        """Ajoute un QComboBox dans la vraie colonne 'Statut' pour chaque ligne après import."""
        for row in range(self.table.rowCount()):
            self.add_status_combobox(row)

    def add_status_combobox(self, row):
        """Ajoute un menu déroulant de statut dans la colonne correspondante."""
        statut_col = self.get_statut_column_index()
        if statut_col is None:
            return  # Ne rien faire si la colonne "Statut" est introuvable

        # ✅ Création du QComboBox avec "Nouveau" comme valeur par défaut
        combobox = QComboBox()
        combobox.addItems(["Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
        combobox.setCurrentText("Nouveau")  # ✅ Toujours définir la valeur par défaut ici

        combobox.currentIndexChanged.connect(partial(self.update_status_value, row, statut_col, combobox))
        self.table.setCellWidget(row, statut_col, combobox)  # ✅ Toujours utiliser setCellWidget

        print(f"✅ QComboBox ajouté à la ligne {row}, colonne {statut_col}")  # ✅ Vérification


    def setup_filters(self):
        """Crée les QComboBox de filtrage et connecte leur signal de changement."""
        self.status_filter = QComboBox()
        self.status_filter.addItems(["Tous", "Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"])
        self.status_filter.currentIndexChanged.connect(self.apply_filters)

        self.formule_filter = QComboBox()
        self.formule_filter.addItems(["Tous", "Solo", "Duo", "Trio", "Full Band"])
        self.formule_filter.currentIndexChanged.connect(self.apply_filters)

        # Ajout au layout (exemple : dans une barre d'outils)
        self.toolbar.addWidget(QLabel("    Statut:"))
        self.toolbar.addWidget(self.status_filter)
        self.toolbar.addWidget(QLabel("    Formule:"))
        self.toolbar.addWidget(self.formule_filter)


    def apply_filters(self):
        """Filtre les lignes du tableau en fonction des valeurs sélectionnées dans les QComboBox."""
        statut_filter = self.status_filter.currentText().strip().lower()
        statut_col = self.get_statut_column_index()

        if statut_col is None:
            print("⚠️ Impossible d'appliquer les filtres : colonne 'Statut' introuvable.")
            return

        statut_order = {
            "Nouveau": 0,
            "Mail envoyé": 1,
            "Échange Tel.": 2,
            "Full": 3,
            "Laisse tomber": 4,
            "Let's Go": 5
        }

        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, statut_col)
            value = widget.currentText().strip().lower() if isinstance(widget, QComboBox) else "Nouveau"

            # ✅ Vérifier si la ligne doit être affichée ou masquée
            show = (statut_filter == "tous") or (value == statut_filter)
            self.table.setRowHidden(row, not show)

    def update_row_color(self, row):
        """Met à jour la couleur de la ligne en fonction du statut sélectionné."""
        statut_col = self.get_statut_column_index()
        if statut_col is None:
            return  # On ne fait rien si la colonne statut est introuvable

        # Récupérer le menu déroulant de la colonne "Statut"
        combobox = self.table.cellWidget(row, statut_col)
        if not combobox:
            return

        statut = combobox.currentText()

        # 🎨 Dictionnaire des couleurs par statut
        colors = {
            "Nouveau": ("#f7f7f7", "black"),  # Blanc
            "Mail envoyé": ("#8ee9f0", "black"),  # Bleu clair
            "Échange Tel.": ("#e0b4f2", "black"),  # Violet
            "Full": ("#fd9595", "black"),  # Rouge clair
            "Laisse tomber": ("#000000", "white"), # Noir avec texte blanc
            "Let's Go": ("#23db6f", "black"),  # Vert
        }

        # Appliquer la couleur correspondante
        if statut in colors:
            bg_color, text_color = colors[statut]
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(QColor(bg_color))
                    item.setForeground(QColor(text_color))


    def setup_formule_column(self):
        """Ajoute une liste déroulante dans la colonne Formule pour chaque ligne existante."""
        for row in range(self.table.rowCount()):
            self.add_formule_combobox(row)

    def add_formule_combobox(self, row):
        """Ajoute un QComboBox dans la colonne 'Formule' de la ligne spécifiée."""
        col_index = self.get_formule_column_index()
        if col_index is not None:  # Vérifie que la colonne "Formule" existe
            combo_box = QComboBox()
            combo_box.addItems(["Solo", "Duo", "Trio", "Full Band"])
            self.table.setCellWidget(row, col_index, combo_box)

    def get_formule_column_index(self):
        """Retourne l'index de la colonne 'Formule'."""
        for col in range(self.table.columnCount()):
            if self.table.horizontalHeaderItem(col) and self.table.horizontalHeaderItem(col).text() == "Formule":
                return col
        return None


    def add_date_button(self, row):
        """Ajoute un bouton '+' qui remplit entièrement la cellule de la colonne 'Date'."""
        date_col = self.get_date_column_index()
        if date_col is None:
            return  # Ne rien faire si la colonne "Date" est introuvable

        # 🔹 Création du bouton "+"
        button = QPushButton("+")
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)  # Remplissage max
        button.setStyleSheet("""
            QPushButton {
                background-color: #0078d7;
                color: white;
                font-size: 16px;
                font-weight: bold;
                border: none;  /* Suppression des bordures */
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)

        # 🔄 Ajuster la hauteur de la ligne pour éviter les coupures
        row_height = max(35, self.table.rowHeight(row))  # Augmenté pour plus de confort
        self.table.setRowHeight(row, row_height)

        # ✅ Forcer la taille du bouton pour qu'il remplisse bien
        button.setFixedHeight(row_height - 2)  # Ajustement fin
        button.setFixedWidth(self.table.columnWidth(date_col) - 2)  # Ajuster à la largeur

        # 📌 Utilisation d'un `QHBoxLayout` pour un centrage optimal
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)  # Supprimer les marges
        layout.setSpacing(0)  # Aucun espacement supplémentaire
        layout.addWidget(button)

        # ✅ Conteneur pour insérer proprement dans la cellule
        container = QWidget()
        container.setLayout(layout)

        button.clicked.connect(lambda: self.open_calendar_popup(row, date_col))

        # 📌 Insérer dans la cellule
        self.table.setCellWidget(row, date_col, container)



    def open_calendar_popup(self, row, col):
        """Affiche un calendrier popup pour sélectionner une date."""
        self.calendar_dialog = QDialog(self)
        self.calendar_dialog.setWindowTitle("Sélectionner une date")
        self.calendar_dialog.setModal(True)
        self.calendar_dialog.setGeometry(500, 300, 400, 300)  # Affiche la fenêtre au centre

        layout = QVBoxLayout()

        # 🗓️ Création du calendrier
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)

        # ✅ Bouton "OK" pour valider la date sélectionnée
        ok_button = QPushButton("OK")
        ok_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
                min-height: 30px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)

        # 💡 Connexion du bouton "OK" pour insérer la date dans la cellule
        ok_button.clicked.connect(lambda: self.set_selected_date(row, col))

        layout.addWidget(self.calendar)
        layout.addWidget(ok_button)

        self.calendar_dialog.setLayout(layout)

        self.calendar_dialog.exec_()  # Affiche la fenêtre en mode bloquant

    def set_selected_date(self, row, col):
        """Remplace le bouton '+' par la date sélectionnée, ajuste la hauteur et la largeur de la cellule."""
        selected_date = self.calendar.selectedDate().toString("dd/MM/yyyy")

        # Vérifie si la cellule contient déjà un widget et le supprime
        if self.table.cellWidget(row, col):
            self.table.removeCellWidget(row, col)

        # Ajoute la date sélectionnée dans la cellule
        item = QTableWidgetItem(selected_date)
        self.table.setItem(row, col, item)

        # 🔄 Ajuster la hauteur de la ligne pour éviter tout chevauchement
        self.table.setRowHeight(row, 30)

        # 📏 Ajuster dynamiquement la largeur de la colonne en fonction du texte affiché
        font_metrics = self.table.fontMetrics()
        text_width = font_metrics.boundingRect(selected_date).width() + 20  # Ajout d'une marge
        self.table.setColumnWidth(col, max(100, text_width))  # Largeur minimale de 100 pixels

        # ✅ Ferme la popup après la sélection
        self.calendar_dialog.accept()


    def setup_date_column(self):
        """Ajoute un bouton '+' dans toutes les cellules de la colonne 'Date'."""
        for row in range(self.table.rowCount()):
            self.add_date_button(row)

    def show_header_menu(self, pos):
        """Affiche un menu contextuel pour trier une colonne."""
        header = self.table.horizontalHeader()
        col = header.logicalIndexAt(pos)
        if col < 0:
            return

        menu = QMenu(self)
        action_asc = menu.addAction("Trier de A à Z")
        action_desc = menu.addAction("Trier de Z à A")
        global_pos = header.viewport().mapToGlobal(pos)
        action = menu.exec_(global_pos)

        if action:
            self.table.setSortingEnabled(True)  # ✅ Active le tri
            if action == action_asc:
                self.table.sortItems(col, Qt.AscendingOrder)
            elif action == action_desc:
                self.table.sortItems(col, Qt.DescendingOrder)

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


    def show_date_filter(self):
        logging.info("show_date_filter non implémenté")
        QMessageBox.information(self, "Filtre Date", "Filtre par date non encore implémenté.")

    def show_location_filter(self):
        logging.info("show_location_filter non implémenté")
        QMessageBox.information(self, "Filtre Lieu", "Filtre par lieu non encore implémenté.")

    def show_status_filter(self):
        """Filtre les lignes en fonction du statut sélectionné."""
        statut_filter, ok = QInputDialog.getItem(
            self, "Filtrer par statut", "Choisissez un statut :", 
            ["Tous", "Nouveau", "Mail envoyé", "Échange Tel.", "Full", "Laisse tomber", "Let's Go"],
            0, False
        )

        if ok:
            statut_col = self.get_statut_column_index()
            for row in range(self.table.rowCount()):
                widget = self.table.cellWidget(row, statut_col)
                value = widget.currentText().strip() if isinstance(widget, QComboBox) else "Nouveau"

                self.table.setRowHidden(row, statut_filter != "Tous" and value != statut_filter)

    def show_price_filter(self):
        logging.info("show_price_filter non implémenté")
        QMessageBox.information(self, "Filtre Cachet", "Filtre par cachet non encore implémenté.")

    def add_row(self):
        """Ajoute une nouvelle ligne et applique le tri immédiatement après."""
        current_row = self.table.rowCount()
        self.table.insertRow(current_row)

        # Ajouter la date actuelle
        date_item = QTableWidgetItem(datetime.now().strftime(config["date_format"]))
        self.table.setItem(current_row, 0, date_item)

        # Ajouter les listes déroulantes pour Statut et Formule
        self.add_status_combobox(current_row)
        self.add_formule_combobox(current_row)

        # Ajouter une cellule vide pour Cachet
        cachet_item = QTableWidgetItem("")
        self.table.setItem(current_row, 2, cachet_item)

        self.adjust_columns()
        self.save_state()

        # ✅ Forcer le tri après ajout
        self.header_view.sort_column(self.get_statut_column_index(), Qt.AscendingOrder)

    def delete_row(self):
        """Supprime les lignes sélectionnées et applique le tri après suppression."""
        selected_rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        for row in selected_rows:
            self.table.removeRow(row)

        # ✅ Forcer le tri après suppression
        self.header_view.sort_column(self.get_statut_column_index(), Qt.AscendingOrder)

        # Ajuster la largeur des colonnes après suppression
        self.adjust_columns()

    def on_table_edit(self, item):
        """Gère l'historique des modifications pour les cellules texte et QComboBox."""
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

    def on_status_change(self, row, combo_box):
        """Gère l'historique des modifications pour les changements de statut."""
        statut_col = self.get_statut_column_index()
        if statut_col is None:
            return

        key = (row, statut_col)
        new_value = combo_box.currentText()

        if key not in self.last_values:
            self.last_values[key] = new_value
        else:
            old_value = self.last_values[key]
            if old_value != new_value:
                self.undo_stack.append((row, statut_col, old_value, new_value))
                self.redo_stack.clear()

            self.last_values[key] = new_value

        # ✅ Appliquer le tri immédiatement après un changement de statut
        self.header_view.sort_column(statut_col, Qt.AscendingOrder)

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
        """Crée l'onglet Carte avec un tableau récapitulatif des lieux exportés et une fenêtre latérale pour les détails d'itinéraire."""
        self.map_tab = QWidget()
        layout = QVBoxLayout(self.map_tab)  

        # 📌 Séparateur pour diviser l'écran en 3/4 carte - 1/4 détails
        splitter = QSplitter(Qt.Horizontal)

        # 🌍 Carte interactive
        self.map_view = QWebEngineView()
        self.map_manager = MapManager(self.map_view, parent=self)  # Instanciation du gestionnaire de carte
        self.map_view.setMinimumWidth(self.width() * 3 // 4)  # 3/4 de la largeur de la fenêtre
        splitter.addWidget(self.map_view)

        # 📝 Fenêtre latérale des détails d'itinéraire
        self.itinerary_details_widget = QWidget()
        self.itinerary_details_widget.setFixedWidth(self.width() // 4)  # 1/4 de la largeur
        self.itinerary_details_layout = QVBoxLayout(self.itinerary_details_widget)
        self.itinerary_details_layout.addWidget(QLabel("<b>Détails du trajet</b>", alignment=Qt.AlignCenter))
        splitter.addWidget(self.itinerary_details_widget)

        layout.addWidget(splitter)

        # 📌 Barre d'outils pour la gestion de la carte
        map_toolbar = QHBoxLayout()  # ✅ Initialisation correcte

        # 📥 Bouton "Exporter PDF"
        self.export_pdf_btn = QPushButton("📄 Exporter en PDF")
        self.export_pdf_btn.clicked.connect(self.export_route)
        map_toolbar.addWidget(self.export_pdf_btn)

        # 📌 Filtrage des lieux affichés
        self.view_type = QComboBox()
        self.view_type.addItems(["Tous les événements", "Confirmés", "En attente", "Cette semaine", "Ce mois"])
        self.view_type.currentTextChanged.connect(self.update_map)
        map_toolbar.addWidget(QLabel("Afficher :"))
        map_toolbar.addWidget(self.view_type)

        # 🚀 Bouton "Créer Itinéraire"
        self.optimize_route_btn = QPushButton("Créer Itinéraire")

        # ✅ Vérifier s'il y a une connexion avant de tenter de déconnecter
        try:
            self.optimize_route_btn.clicked.disconnect()
        except TypeError:
            pass  # Ignore l'erreur si aucune connexion existante

        self.optimize_route_btn.clicked.connect(self.create_itinerary)
        map_toolbar.addWidget(self.optimize_route_btn)

        # ✅ Ajouter `map_toolbar` une seule fois
        layout.addLayout(map_toolbar)

        # 📌 Barre de progression pour le géocodage
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 📊 Tableau des lieux envoyés vers la carte
        self.map_table = QTableWidget()
        self.map_table.setColumnCount(4)
        self.map_table.setHorizontalHeaderLabels(["Contact", "Adresse", "Statut", "Coordonnées"])
        self.map_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.map_table)

        # ✅ Activer le déplacement des lignes par glisser-déposer
        self.map_table.setDragDropMode(QAbstractItemView.InternalMove)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.map_table.setDragEnabled(True)
        self.map_table.setAcceptDrops(True)
        self.map_table.viewport().setAcceptDrops(True)
        self.map_table.setDropIndicatorShown(True)

        # 🔄 Boutons d'action
        buttons_layout = QHBoxLayout()

        self.clear_map_table_btn = QPushButton("🗑️ Effacer la liste")
        self.clear_map_table_btn.clicked.connect(self.clear_map_table)
        buttons_layout.addWidget(self.clear_map_table_btn)

        self.delete_map_table_row_btn = QPushButton("❌ Supprimer la ligne sélectionnée")
        self.delete_map_table_row_btn.clicked.connect(self.delete_selected_map_row)
        buttons_layout.addWidget(self.delete_map_table_row_btn)

        layout.addLayout(buttons_layout)

        self.map_tab.setLayout(layout)

        # 🌍 Initialisation de la carte
        self.initialize_map()


    def delete_selected_map_row(self):
        """Supprime la ligne sélectionnée dans le tableau des marqueurs sur la carte."""
        selected_rows = sorted(set(index.row() for index in self.map_table.selectedIndexes()), reverse=True)
        for row in selected_rows:
            self.map_table.removeRow(row)


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
        """Initialise la carte une seule fois."""
        if not hasattr(self, "map"):
            self.map = folium.Map(
                location=[46.2276, 2.2137],
                zoom_start=6,
                tiles="OpenStreetMap"
            )
            self.marker_cluster = MarkerCluster().add_to(self.map)

        # Mettre à jour l'affichage de la carte
        self.map_manager.update_map()

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
        """Met à jour l'affichage de la carte sans la réinitialiser."""
        self.map_manager.update_map()


    def optimize_route(self):
        """Optimise l'itinéraire entre tous les contacts affichés sur la carte."""
        contacts = self.get_displayed_contacts()
        
        if len(contacts) < 2:
            QMessageBox.warning(self, "Erreur", "Il faut au moins deux lieux pour créer un itinéraire.")
            return
        
        # Trier les lieux selon la meilleure distance (exemple d'optimisation basique)
        sorted_contacts = sorted(contacts, key=lambda x: (x[1], x[2]))

        # Affichage du résultat
        print("📍 Itinéraire optimisé :", [c[0] for c in sorted_contacts])

        # Afficher l'itinéraire sur la carte
        self.display_route_on_map(sorted_contacts)

    def display_route_on_map(self, sorted_contacts):
        """Ajoute les contacts à la carte sans écraser les anciens."""
        
        # Ne recrée PAS une nouvelle carte chaque fois !
        if not hasattr(self, "map"):
            self.map = folium.Map(location=[sorted_contacts[0][1], sorted_contacts[0][2]], zoom_start=8)
            self.marker_cluster = MarkerCluster().add_to(self.map)

        coordinates_list = []

        for contact, lat, lon in sorted_contacts:
            folium.Marker(
                location=[lat, lon],
                popup=f"{contact}",
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(self.marker_cluster)
            coordinates_list.append([lat, lon])

        # Tracer une ligne entre les points
        folium.PolyLine(coordinates_list, color="red", weight=5, opacity=0.7).add_to(self.map)

        # Mettre à jour l'affichage
        data = io.BytesIO()
        self.map.save(data, close_file=False)
        self.map_view.setHtml(data.getvalue().decode())

        print("✅ Tous les marqueurs ont été ajoutés sur la carte.")


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
        """Génère une feuille de route en PDF avec QR Code."""
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
            title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=24, spaceAfter=30)
            header_style = ParagraphStyle('CustomHeader', parent=styles['Heading2'], fontSize=14, spaceAfter=12)
            normal_style = styles["Normal"]

            story = []
            story.append(Paragraph("Feuille de Route", title_style))
            story.append(Spacer(1, 12))

            user_name = config.get("user", "Utilisateur inconnu")
            story.append(Paragraph(f"Généré par: {user_name}", normal_style))
            story.append(Paragraph(f"Date de création: {datetime.now().strftime('%d/%m/%Y %H:%M')}", normal_style))
            story.append(Spacer(1, 20))

            selected_rows = self.get_selected_rows()
            if not selected_rows:
                QMessageBox.warning(self, "Erreur", "Aucune ligne sélectionnée pour la feuille de route.")
                return

            for row in selected_rows:
                event_date = self.get_cell_text(row, 0) or "Non spécifié"
                event_location = self.get_cell_text(row, 1) or "Non spécifié"
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

            # Génération du QR Code
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
            qr_data = f"Feuille de route générée le {datetime.now().strftime('%d/%m/%Y %H:%M')} par {user_name}"
            qr.add_data(qr_data)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")

            qr_path = "temp_qr.png"
            qr_img.save(qr_path)

            try:
                story.append(Paragraph("QR Code de validation:", normal_style))
                story.append(Image(qr_path, width=100, height=100))
                doc.build(story)
            finally:
                if os.path.exists(qr_path):
                    os.remove(qr_path)

            QMessageBox.information(self, "Succès", "Feuille de route générée avec succès!")

        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors de la génération du document : {str(e)}")
            logging.error(f"Erreur génération feuille de route: {str(e)}")

    def export_data(self):
        """Menu d'exportation des données."""
        file_path, _ = QFileDialog.getSaveFileName(self, "Exporter les données", "", "Fichiers CSV (*.csv);;Fichiers Excel (*.xlsx)")
        if file_path.endswith(".csv"):
            self.export_csv(file_path)
        elif file_path.endswith(".xlsx"):
            self.export_excel(file_path)

    def export_excel(self, file_path=None):
        """Exporter les données au format Excel."""
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(self, "Exporter en Excel", "", "Fichiers Excel (*.xlsx)")
            if not file_path:
                return

        wb = Workbook()
        ws = wb.active
        headers = [self.table.horizontalHeaderItem(col).text() if self.table.horizontalHeaderItem(col) else f"Colonne {col}" for col in range(self.table.columnCount())]
        ws.append(headers)
        
        for row in range(self.table.rowCount()):
            ws.append([self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())])
        
        wb.save(file_path)
        QMessageBox.information(self, "Export Excel", "Export en Excel réussi !")

    def export_csv(self, file_path):
        """Exporter les données au format CSV."""
        df = pd.DataFrame([[self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())] for row in range(self.table.rowCount())])
        df.to_csv(file_path, index=False, header=[self.table.horizontalHeaderItem(col).text() for col in range(self.table.columnCount())])
        QMessageBox.information(self, "Export CSV", "Export en CSV réussi !")

    def export_pdf(self):
        """Exporter les données au format PDF."""
        file_path, _ = QFileDialog.getSaveFileName(self, "Exporter en PDF", "", "Fichiers PDF (*.pdf)")
        if not file_path:
            return
        
        pdf = canvas.Canvas(file_path)
        pdf.drawString(100, 800, "Export des réservations")
        y = 780
        
        for row in range(self.table.rowCount()):
            line = " | ".join(self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount()))
            pdf.drawString(100, y, line)
            y -= 20
        
        pdf.save()
        QMessageBox.information(self, "Export PDF", "Export en PDF réussi !")

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
        """Créer un nouveau fichier de réservation."""
        self.current_file = None
        self.table.setRowCount(0)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Date", "Statut", "Cachet"])
        QMessageBox.information(self, "Nouveau fichier", "Un nouveau fichier a été créé.")

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
        """Enregistrer le fichier actuel."""
        if not self.current_file:
            self.current_file, _ = QFileDialog.getSaveFileName(self, "Enregistrer sous", "", "Fichiers CSV (*.csv);;Fichiers Excel (*.xlsx)")
        
        if self.current_file:
            if self.current_file.endswith(".csv"):
                self.export_csv(self.current_file)
            elif self.current_file.endswith(".xlsx"):
                self.export_excel(self.current_file)
            QMessageBox.information(self, "Enregistrement", "Fichier enregistré avec succès !")

    def load_table_data(self, data):
        """Charge les données dans la table"""
        self.table.setRowCount(0)
        for row_data in data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate(row_data):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def get_table_data(self):
        """Récupère les données de la table sous forme de liste de dictionnaires."""
        data = []
        for row in range(self.table.rowCount()):
            row_data = []

            for col in range(self.table.columnCount()):
                if col == 1:  # Colonne "Statut"
                    widget = self.table.cellWidget(row, col)
                    value = widget.currentText() if widget else ""
                elif col == 3:  # ✅ Colonne "Formule"
                    widget = self.table.cellWidget(row, col)
                    value = widget.currentText() if widget else ""
                else:
                    value = self.get_cell_text(row, col)

                row_data.append(value)

            data.append(row_data)
        return data


    def get_selected_events(self):
        """Récupère les événements sélectionnés"""
        selected_rows = self.get_selected_rows()
        return [self.get_table_data()[row] for row in selected_rows]

    def show_itinerary_details(self, route_details):
        """Met à jour la fenêtre latérale avec les détails du trajet."""

        print("🔍 Mise à jour des détails de l'itinéraire...")  # Debug

        # 🔄 Efface bien les anciens détails pour éviter les doublons
        while self.itinerary_details_layout.count() > 0:
            item = self.itinerary_details_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 🔹 Vérifier si le widget parent existe et forcer une largeur plus grande
        parent_widget = self.itinerary_details_layout.parentWidget()
        if parent_widget:
            parent_widget.setMinimumWidth(700)  # ✅ Augmente encore la largeur minimale
            parent_widget.setFixedWidth(750)  # ✅ Force une largeur fixe plus grande

        # 🔹 Vérifier si des étapes existent
        if not route_details or len(route_details) < 2:
            print("⚠️ Aucune donnée d'itinéraire à afficher !")  # Debug
            return

        # 📝 Ajout du titre
        title_label = QLabel("<b>Détails du trajet</b>")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        self.itinerary_details_layout.addWidget(title_label)

        # 🔹 Affichage des étapes du trajet avec un meilleur style
        for index, step in enumerate(route_details[:-1]):  # Le dernier élément contient les coûts de carburant
            formatted_duration = self.format_duration(float(step['duration']))  # ✅ Conversion du temps

            text = f"🚗 {step['from']} ➝ {step['to']} : {formatted_duration} ({step['distance']} km)"
            print(f"Ajout du label : {text}")  # Debug

            label = QLabel(text)

            # ✅ Style dynamique amélioré avec largeur plus grande
            label.setStyleSheet("""
                font-size: 14px;
                font-weight: bold;
                color: white;
                background-color: #0078D7;
                padding: 10px;
                border-radius: 8px;
                border: 2px solid #005A9E;
                min-width: 650px;
                max-width: 750px;
                margin-bottom: 5px;
                text-align: center;
            """)

            self.itinerary_details_layout.addWidget(label)

        # ⛽️ Affichage du coût du carburant
        cost_petrol = route_details[-1]["cost_petrol"]
        cost_diesel = route_details[-1]["cost_diesel"]
        fuel_label = QLabel(f"⛽ Essence : {cost_petrol} € | Diesel : {cost_diesel} €")
        fuel_label.setStyleSheet("font-weight: bold; color: #0078d7; margin-top: 10px;")  # 🔵 Bleu pour le carburant
        self.itinerary_details_layout.addWidget(fuel_label)

        # 🔄 Mise à jour
        self.itinerary_details_widget.update()


    def format_duration(self, minutes):
        """Convertit une durée en minutes décimales en format hh h mm min."""
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if hours > 0:
            return f"{hours}h {mins:02d}min"
        return f"{mins}min"

    def create_itinerary(self):
        """Génère un itinéraire et affiche un overlay de chargement sur la carte."""
        itinerary = self.get_itinerary()
        if len(itinerary) < 2:
            QMessageBox.warning(self, "Itinéraire", "Il faut au moins deux lieux pour créer un itinéraire.")
            return

        # 💬 Affichage de l'overlay de chargement sur la carte
        self.show_loading_on_map()

        # Réinitialisation de la barre de progression
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        QApplication.processEvents()

        # Extraire les coordonnées
        points = [self.get_coordinates(row[2]) for row in itinerary if row[2] != "Non localisé"]
        if len(points) < 2:
            QMessageBox.warning(self, "Itinéraire", "Impossible d'obtenir les coordonnées des lieux.")
            self.progress_bar.setVisible(False)
            self.hide_loading_on_map()
            return

        # 🔥 Définition des icônes pour les étapes
        icons = {0: "🎤", len(points) - 1: "🏁"}

        # Ajout des points sur la carte + mise à jour de la barre de progression
        total_steps = len(points)
        for i, (lat, lon) in enumerate(points):
            icon = icons.get(i, "📍")
            self.map_manager.add_marker(f"{icon} Étape {i+1}", lat, lon, "Itinéraire")

            # Mise à jour de la barre de progression
            self.progress_bar.setValue(int((i + 1) / total_steps * 100))
            QApplication.processEvents()

        # ✅ Vérifier que `self.map_manager` existe bien avant d’appeler `add_route_to_map`
        if hasattr(self, "map_manager") and hasattr(self.map_manager, "add_route_to_map"):
            self.map_manager.add_route_to_map(self.map_manager.map, points)
        else:
            logging.error("❌ Erreur : `map_manager` ou `add_route_to_map` est introuvable !")

        # Calculer les temps de trajet et coûts de carburant
        route_details = self.calculate_route_details(points)

        # Afficher les détails dans la fenêtre latérale
        self.show_itinerary_details(route_details)

        # 🔄 Masquage de l'overlay une fois terminé
        self.hide_loading_on_map()

        # Masquer la barre de progression après 1 seconde
        self.progress_bar.setValue(100)
        QTimer.singleShot(1000, lambda: self.progress_bar.setVisible(False))

        # 🔄 Mettre à jour la carte après avoir ajouté l'itinéraire
        self.update_map_display()

    def show_loading_on_map(self):
        """Ajoute un overlay semi-transparent avec un message de chargement sur la carte."""
        overlay_script = """
            if (!document.getElementById('loading-overlay')) {
                var loadingOverlay = document.createElement('div');
                loadingOverlay.id = 'loading-overlay';
                loadingOverlay.style.position = 'absolute';
                loadingOverlay.style.top = '0';
                loadingOverlay.style.left = '0';
                loadingOverlay.style.width = '100%';
                loadingOverlay.style.height = '100%';
                loadingOverlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
                loadingOverlay.style.display = 'flex';
                loadingOverlay.style.alignItems = 'center';
                loadingOverlay.style.justifyContent = 'center';
                loadingOverlay.style.fontSize = '24px';
                loadingOverlay.style.color = 'white';
                loadingOverlay.style.zIndex = '1000';
                loadingOverlay.innerText = '🔄 Chargement de l’itinéraire...';
                document.body.appendChild(loadingOverlay);
            }
        """
        self.map_view.page().runJavaScript(overlay_script)

    def hide_loading_on_map(self):
        """Supprime l'overlay de chargement."""
        remove_script = "document.getElementById('loading-overlay')?.remove();"
        self.map_view.page().runJavaScript(remove_script)


    def calculate_route_details(self, points):
        """Calcule les distances, durées et coût du carburant pour le trajet."""
        total_distance = 0
        total_duration = 0
        details = []

        for i in range(len(points) - 1):
            start, end = points[i], points[i + 1]
            geometry, duration, distance = self.map_manager.get_route(start, end)  # ✅ Appel correct via MapManager

            if not geometry:
                continue

            total_distance += distance / 1000  # Conversion mètres ➝ km
            total_duration += duration / 60  # Conversion secondes ➝ minutes
            details.append({
                "from": f"Étape {i + 1}",
                "to": f"Étape {i + 2}",
                "distance": round(distance / 1000, 2),
                "duration": round(duration / 60, 2)
            })

        # Estimation du coût du carburant
        consommation_moyenne = 7.5  # L/100km
        prix_essence = 1.85  # €/L
        prix_diesel = 1.75  # €/L

        cost_petrol = round((total_distance / 100) * consommation_moyenne * prix_essence, 2)
        cost_diesel = round((total_distance / 100) * consommation_moyenne * prix_diesel, 2)

        details.append({"cost_petrol": cost_petrol, "cost_diesel": cost_diesel})

        return details

    def calculate_optimized_route(self, events):
        """Calcule l'itinéraire optimisé"""
        # Implémentation d'un algorithme de TSP (Traveling Salesman Problem)
        # pour optimiser l'itinéraire entre les événements sélectionnés
        return events  # Retourne les événements dans l'ordre optimisé

    def display_optimized_route(self, route):
        """Affiche l'itinéraire optimisé sur la carte"""
        # Implémentation pour afficher l'itinéraire optimisé sur la carte
        pass

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