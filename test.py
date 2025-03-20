import os
import json
import threading
from datetime import datetime


import vosk
import pyaudio

from kivy.lang import Builder

from kivy.clock import Clock
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.uix.screenmanager import Screen

from kivymd.app import MDApp
from kivymd.uix.card import MDCard
from kivymd.uix.toolbar import MDTopAppBar
from kivymd.uix.button import MDRaisedButton, MDFloatingActionButton
from kivymd.uix.list import OneLineListItem, ThreeLineListItem
from kivymd.uix.dialog import MDDialog
from kivy.uix.boxlayout import BoxLayout
from kivymd.uix.button import MDIconButton


class MainScreen(Screen):
    pass

class NotesScreen(Screen):
    pass

class FrereAssistantApp(MDApp):
    def build(self):
        self.theme_cls.primary_palette = "Blue"
        self.recording_opacity = 0
        self.recording = False
        self.default_folder = "Notes"
        self.notes_file = "notes.txt"
        self.ensure_storage()
        return Builder.load_file('design.kv')

    def ensure_storage(self):
        os.makedirs(self.default_folder, exist_ok=True)
        notes_path = os.path.join(self.default_folder, self.notes_file)
        if not os.path.exists(notes_path):
            with open(notes_path, 'w', encoding='utf-8') as f:
                f.write("Bienvenue dans votre gestionnaire de notes.\n")

    def on_start(self):
        self.update_folder_list()
        self.start_hotword_listener()

    # Gestion enregistrement vocal
    def toggle_recording(self):
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_hotword_listener(self):
        # Initialise le modèle et l'audio pour l'écoute du hotword
        try:
            self.hotword_model = vosk.Model("model-fr")
        except Exception as e:
            self.show_error("Modèle Vosk introuvable pour hotword !")
            return
        try:
            self.hotword_recognizer = vosk.KaldiRecognizer(self.hotword_model, 16000)
            self.hotword_audio = pyaudio.PyAudio()
            self.hotword_stream = self.hotword_audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=4096
            )
        except Exception as e:
            self.show_error(f"Erreur audio pour hotword : {str(e)}")
            return
        self.hotword_listening = True
        self.hotword_thread = threading.Thread(target=self.process_hotword, daemon=True)
        self.hotword_thread.start()

    def process_hotword(self):
        import unicodedata
        import time
        # Boucle d'écoute en arrière-plan avec vérification de l'écran courant
        while self.hotword_listening:
            # Ne traiter le hotword que si l'on est sur l'écran principal
            if self.root.current != "main":
                time.sleep(0.5)
                continue
            try:
                data = self.hotword_stream.read(4096, exception_on_overflow=False)
                print("Hotword: données audio capturées :", len(data))
                if self.hotword_recognizer.AcceptWaveform(data):
                    result = json.loads(self.hotword_recognizer.Result()).get('text', '').strip().lower()
                    normalized_result = unicodedata.normalize('NFD', result)
                    normalized_result = normalized_result.encode('ascii', 'ignore').decode("utf-8")
                    print("Hotword: résultat:", normalized_result)
                    if "vas-y frere" in normalized_result:
                        print("Hotword détecté !")
                        Clock.schedule_once(lambda dt: self.activate_app(), 0)
            except Exception as e:
                print("Hotword: erreur:", e)
                pass

    def activate_app(self):
        if self.root.current != "main":
            self.root.current = "main"
        if not self.recording:
            self.toggle_recording()

    def stop_hotword_listener(self):
        # Arrête proprement le thread d'écoute du hotword
        self.hotword_listening = False
        if hasattr(self, 'hotword_stream'):
            self.hotword_stream.stop_stream()
            self.hotword_stream.close()
        if hasattr(self, 'hotword_audio'):
            self.hotword_audio.terminate()

    def start_recording(self):
        model_path = "model-fr"
        if not os.path.exists(model_path):
            self.show_error("Modèle Vosk introuvable !")
            return

        try:
            self.model = vosk.Model(model_path)
            self.recognizer = vosk.KaldiRecognizer(self.model, 16000)
            self.audio = pyaudio.PyAudio()
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=4096
            )
        except OSError as e:
            self.show_error(f"Erreur audio : périphérique non détecté ({str(e)})")
            return
        except Exception as e:
            self.show_error(f"Erreur audio inconnue : {str(e)}")
            return

        # Ajout d'une variable pour accumuler la transcription
        self.transcript = ""
        self.recording_event = threading.Event()
        self.recording_event.clear()

        self.progress_value = 0
        Clock.schedule_interval(self.update_progress_bar, 0.1)

        self.recording = True
        self.recording_opacity = 1
        self.recording_dialog = MDDialog(
            title="Enregistrement en cours...",
            buttons=[MDRaisedButton(text="STOP", on_release=lambda x: self.stop_recording())]
        )
        self.recording_dialog.open()
        self.audio_thread = threading.Thread(target=self.process_audio, daemon=True)
        self.audio_thread.start()
        Clock.schedule_interval(self.update_indicator, 0.5)

    def update_progress_bar(self, dt):
        # Animation simple : incrémentation de la barre de 0 à 100 se réinitialisant ensuite
        self.progress_value += 2
        if self.progress_value > 100:
            self.progress_value = 0
        self.root.get_screen("main").ids.recognition_progress.value = self.progress_value

    def process_audio(self):
        while self.recording and not self.recording_event.is_set():
            try:
                data = self.stream.read(4096, exception_on_overflow=False)
                if self.recognizer.AcceptWaveform(data):
                    result = json.loads(self.recognizer.Result()).get('text', '').strip()
                    if result:
                        # Accumuler la transcription au lieu de sauvegarder automatiquement la note
                        self.transcript += " " + result
                else:
                    pass
            except Exception as e:
                Clock.schedule_once(lambda dt: self.show_error(f"Erreur traitement audio: {str(e)}"), 0)
                break

    def stop_recording(self, *args):
        if self.recording:
            self.recording = False
            self.recording_event.set()  # Signale l'arrêt du thread
            self.recording_opacity = 0
            if hasattr(self, 'stream') and self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
            if hasattr(self, 'audio') and self.audio is not None:
                self.audio.terminate()
            if hasattr(self, 'recording_dialog') and self.recording_dialog is not None:
                self.recording_dialog.dismiss()
            # Attendre que le thread se termine proprement
            if hasattr(self, 'audio_thread') and self.audio_thread.is_alive():
                self.audio_thread.join(timeout=1)
            # Arrêter l'animation de la barre de progression et l'indicateur
            Clock.unschedule(self.update_progress_bar)
            Clock.unschedule(self.update_indicator)
            # Sauvegarder la transcription accumulée lorsque l'utilisateur arrête manuellement l'enregistrement
            if self.transcript.strip():
                self.save_note(self.transcript.strip())
                self.transcript = ""
            self.update_folder_list()

    def update_indicator(self, dt):
        # Utilisation d'un toggle simple pour créer un effet de clignotement
        self.recording_opacity = 1 if self.recording_opacity == 0 else 0
        self.root.get_screen("main").ids.recording_indicator.text_color = (1, 0, 0, self.recording_opacity)

    def save_note(self, text):
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M]")
        notes_path = os.path.join(self.default_folder, self.notes_file)

        print(f"💾 Sauvegarde de la note : {text}")  # Débogage

        try:
            with open(notes_path, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp} - {text}\n")

            print("✅ Note sauvegardée avec succès !")  # Débogage
            Clock.schedule_once(lambda dt: self.open_notes(), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.show_error(f"Erreur sauvegarde: {str(e)}"), 0)

    def open_notes(self):
        from kivy.uix.boxlayout import BoxLayout  # Assurer l'importation si nécessaire

        notes_path = os.path.join(self.default_folder, self.notes_file)
        note_list = self.root.get_screen("notes").ids.note_list
        note_list.clear_widgets()

        print("Chargement des notes...")  # Débogage

        try:
            with open(notes_path, 'r', encoding='utf-8') as f:
                notes = f.readlines()

            if not notes:
                note_list.add_widget(OneLineListItem(text="Aucune note enregistrée."))
                return

            print(f"Nombre de notes chargées : {len(notes)}")  # Débogage

            for idx, line in enumerate(notes):
                if ' - ' in line:
                    timestamp, text = line.split(' - ', 1)

                    # Conteneur principal pour organiser les éléments
                    box = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(56))

                    # Élément principal de la note
                    item = ThreeLineListItem(
                        text=text.strip(),
                        secondary_text=f"Créé le {timestamp.strip('[]')}",
                    )

                    # Bouton Modifier
                    edit_btn = MDIconButton(
                        icon="pencil",
                        theme_text_color="Custom",
                        text_color=(0, 0, 1, 1),  # Bleu
                        on_release=lambda _, i=idx: self.edit_dialog(i)
                    )

                    # Bouton Supprimer
                    delete_btn = MDIconButton(
                        icon="delete",
                        theme_text_color="Custom",
                        text_color=(1, 0, 0, 1),  # Rouge
                        on_release=lambda _, i=idx: self.delete_note(i)
                    )

                    # Ajouter les widgets au conteneur BoxLayout
                    box.add_widget(item)
                    box.add_widget(edit_btn)
                    box.add_widget(delete_btn)

                    # Ajouter la ligne complète à la liste
                    note_list.add_widget(box)

        except Exception as e:
            self.show_error(f"Erreur lecture notes: {str(e)}")

        self.root.current = "notes"

    def update_note(self, index, new_text):
        notes_path = os.path.join(self.default_folder, self.notes_file)
        try:
            with open(notes_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if 0 <= index < len(lines):
                timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M]")
                lines[index] = f"{timestamp} - {new_text}\n"
                with open(notes_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                if hasattr(self, 'edit_dialog_box') and self.edit_dialog_box:
                    self.edit_dialog_box.dismiss()
                self.open_notes()
            else:
                self.show_error("Index de note invalide.")
        except Exception as e:
            self.show_error(f"Erreur mise à jour: {str(e)}")

    def delete_note(self, index):
        def confirm_delete(_):
            notes_path = os.path.join(self.default_folder, self.notes_file)
            try:
                with open(notes_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                if 0 <= index < len(lines):
                    del lines[index]
                    with open(notes_path, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                    if self.confirm_dialog:
                        self.confirm_dialog.dismiss()
                    self.open_notes()
                else:
                    self.show_error("Index de note invalide.")
            except Exception as e:
                self.show_error(f"Erreur suppression: {str(e)}")

        if hasattr(self, 'confirm_dialog') and self.confirm_dialog:
            self.confirm_dialog.dismiss()

        self.confirm_dialog = MDDialog(
            title="Confirmer la suppression",
            text="Es-tu sûr de vouloir supprimer cette note ?",
            buttons=[
                MDRaisedButton(text="Annuler", on_release=lambda x: self.confirm_dialog.dismiss()),
                MDRaisedButton(text="Supprimer", on_release=confirm_delete),
            ],
        )
        self.confirm_dialog.open()

    def export_notes(self, format="txt"):
        if format not in ["txt", "json"]:
            self.show_error("Format non supporté.")
            return False

        notes_path = os.path.join(self.default_folder, self.notes_file)
        export_path = os.path.join(self.default_folder, f"notes_export.{format}")

        try:
            with open(notes_path, 'r', encoding='utf-8') as f:
                notes = f.readlines()

            if format == "txt":
                with open(export_path, 'w', encoding='utf-8') as f:
                    f.writelines(notes)
            elif format == "json":
                notes_data = []
                for line in notes:
                    if ' - ' in line:
                        parts = line.split(' - ', 1)
                        notes_data.append({"timestamp": parts[0].strip(), "text": parts[1].strip()})
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(notes_data, f, indent=4, ensure_ascii=False)

            self.show_message(f"Notes exportées sous {export_path}")
            return True
        except Exception as e:
            self.show_error(f"Erreur exportation: {str(e)}")
            return False

    def show_message(self, message):
        self.message_dialog = MDDialog(
            title="Information",
            text=message,
            buttons=[MDRaisedButton(text="OK", on_release=lambda x: self.message_dialog.dismiss())]
        )
        self.message_dialog.open()

    def edit_dialog(self, index):
        notes_path = os.path.join(self.default_folder, self.notes_file)
        try:
            with open(notes_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if index >= len(lines):
                return

            note_text = lines[index].split(' - ', 1)[1].strip()

            content = Builder.load_string('''
BoxLayout:
    orientation: "vertical"
    spacing: "10dp"
    padding: "10dp"
    size_hint_y: None
    height: "400dp"

    ScrollView:
        size_hint_y: None
        height: "380dp"  # Limite l'affichage de la note et ajoute un scroll si nécessaire

        MDTextField:
            id: note_input
            hint_text: "Texte de la note"
            text: ""
            multiline: True
            size_hint_x: 1
            size_hint_y: None
            height: self.minimum_height
            mode: "fill"
            ''')

            Clock.schedule_once(lambda dt: setattr(content.ids.note_input, "text", note_text), 0.1)

            self.edit_dialog_box = MDDialog(
                title="Modifier la note",  # Le titre reste fixe
                type="custom",
                content_cls=content,
                size_hint=(0.9, None),  # Largeur 90% de l'écran, hauteur ajustable
                buttons=[
                    MDRaisedButton(text="Annuler", on_release=lambda x: self.edit_dialog_box.dismiss()),
                    MDRaisedButton(text="Supprimer", on_release=lambda x: self.delete_note(index)),
                    MDRaisedButton(text="Sauvegarder", on_release=lambda x: self.update_note(index, content.ids.note_input.text))
                ]
            )
            self.edit_dialog_box.open()
        except Exception as e:
            self.show_error(f"Erreur édition: {str(e)}")

    def update_folder_list(self):
        folder_list = self.root.get_screen("main").ids.folder_list
        folder_list.clear_widgets()
        folder_list.add_widget(OneLineListItem(
            text=self.default_folder,
            on_release=lambda x: self.open_notes()
        ))

    def show_error(self, message):
        def close_dialog(_):
            self.error_dialog.dismiss()

        self.error_dialog = MDDialog(
            title="Erreur",
            text=message,
            buttons=[MDRaisedButton(text="OK", on_release=close_dialog)]
        )
        self.error_dialog.open()

    def go_back(self):
        self.root.current = "main"

    def open_notes_screen(self):
        self.open_notes()  # Recharge les notes avant d'afficher l'écran
        self.root.current = "notes"

if __name__ == "__main__":
    FrereAssistantApp().run()