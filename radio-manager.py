import sys
import json
import configparser
import charset_normalizer 
import re  
import threading
import requests
import socket
from pathlib import Path
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput



RENAME_VARIABLES = ["[REALNAME]", "[OLDNAME]", "[BITRATE]", "[CODEC]", "[GENRE]"]
DEFAULT_RENAME_TEMPLATE = "[REALNAME] [[CODEC] - [BITRATE]] ([GENRE])"


class DataProcessor:
    def __init__(self):
        self.log_messages = []
    
    def log(self, message):
        self.log_messages.append(message)
    
    def process_csv_file(self, file_path):
        """Обработка CSV файла с валидацией"""
        self.log_messages = []
        stations = []
        error_count = 0
        success_count = 0
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            lines = content.splitlines()
            
            for line_num, line in enumerate(lines, 1):
                try:
                    line = line.strip().lstrip('\ufeff')
                    if not line:
                        continue
                    
                    parts = re.split(r'\t+|\s{2,}', line)
                    
                    if len(parts) == 1 and ' ' in parts[0]:
                        if parts[0].count('http') >= 2:
                            match = re.match(r'^(.*?)\s+(https?://.*?)\s+(https?://.*?)(?:\s+(-?\d+))?$', parts[0])
                            if match:
                                name, url1, url2, volume = match.groups()
                                url = url1 if url1 == url2 else url1
                                volume_int = int(volume) if volume else 0
                                if volume_int < -64 or volume_int > 64:
                                    self.log(f"Строка {line_num}: Громкость {volume_int} вне диапазона, установлена в 0")
                                    volume_int = 0
                            else:
                                raise ValueError("Неправильный формат строки")
                        else:
                            space_parts = parts[0].rsplit(' ', 2)
                            if len(space_parts) >= 3:
                                name = ' '.join(space_parts[:-2])
                                url = space_parts[-2]
                                volume = space_parts[-1]
                            else:
                                raise ValueError("Недостаточно частей в строке")
                    elif len(parts) >= 3:
                        name = ' '.join(parts[:-2])
                        url = parts[-2]
                        volume = parts[-1]
                    else:
                        raise ValueError("Недостаточно частей в строке")
                    
                    # Подготовка данных
                    name = name.strip()
                    url = url.strip().replace(' ', '')  # удаление пробелов из URL
                    try:
                        volume_int = int(volume) if volume else 0
                        if volume_int < -64 or volume_int > 64:
                            self.log(f"Строка {line_num}: Громкость {volume_int} вне диапазона, установлена в 0")
                            volume_int = 0
                    except ValueError:
                        self.log(f"Строка {line_num}: Неверная громкость '{volume}', установлена в 0")
                        volume_int = 0
                    
                    if not re.match(r'^https?://', url):
                        raise ValueError(f"Неправильный формат URL '{url}'")
                    
                    stations.append({
                        'name': name,
                        'url': url,
                        'volume': volume_int
                    })
                    success_count += 1
                    
                except Exception as e:
                    error_count += 1
                    self.log(f"Строка {line_num}: Ошибка: {str(e)}")
            
            # Финальная статистика
            self.log(f"Обработано: {success_count} успешно, {error_count} ошибок")
            
        except Exception as e:
            self.log(f"Ошибка при обработке файла: {str(e)}")
        
        return stations, self.log_messages
    
    def save_csv_file(self, file_path, stations):
        """Сохранение станций в CSV файл"""
        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                for station in stations:
                    line = f"{station['name']}\t{station['url']}\t{station['volume']}\r\n"
                    f.write(line)
            return True, f"Файл сохранён: {file_path}"
        except Exception as e:
            return False, f"Ошибка при сохранении файла: {str(e)}"


class StatusBar(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setFixedHeight(24)
        
        # Текстовое поле для сообщений
        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.message_label.setStyleSheet("QLabel { padding: 0 5px; }")
        self.message_label.setFixedWidth(300)  # Фиксированная ширина
        self.message_label.setWordWrap(False)
        self.addWidget(self.message_label)
        
        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        # self.progress_bar.setFixedWidth(300)
        self.progress_bar.setFixedHeight(16)
        #self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.addWidget(self.progress_bar, 1)
        
    def show_message(self, text, timeout=0):
        """Показать сообщение в статус-баре"""
        self.message_label.setText(text)
        if timeout > 0:
            QTimer.singleShot(timeout * 1000, self.clear_message)
    
    def clear_message(self):
        """Очистить сообщение в статус-баре"""
        self.message_label.setText("")
    
    def show_progress(self, show=True):
        """Показать/скрыть прогресс-бар"""
        self.progress_bar.setVisible(show)
        if not show:
            self.progress_bar.setValue(0)
    
    def set_progress(self, value):
        """Установить значение прогресс-бара"""
        self.progress_bar.setValue(value)
    
    def set_progress_range(self, min_val, max_val):
        """Установить диапазон прогресс-бара"""
        self.progress_bar.setRange(min_val, max_val)


class ConfigManager:
    CONFIG_FILE = "options.ini"

    @staticmethod
    def load_config():
        config = configparser.ConfigParser()
        if Path(ConfigManager.CONFIG_FILE).exists():
            config.read(ConfigManager.CONFIG_FILE)
        else:
            # Создаем конфиг с настройками по умолчанию
            config['Settings'] = {
                'theme': 'light',
                'window_width': '800',
                'window_height': '600',
                'player_volume': '0.5',
                'max_check_threads': '10',
                'check_timeout': '10',
                'delete_404': 'true',
                'delete_Error': 'true',
                'delete_ConnError': 'true',
                'delete_Timeout': 'true',
                'rename_template': DEFAULT_RENAME_TEMPLATE
            }
            ConfigManager.save_config(config)
        return config
    
    @staticmethod
    def save_config(config):
        with open(ConfigManager.CONFIG_FILE, 'w') as configfile:
            config.write(configfile)


class ThemeManager:

    COMMON_STYLE = """
        /*Поле поиска*/               
        QLineEdit.search{
            padding: 5px;
            font-size: 14px;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            padding-right: 60px;
            margin-right: 12px; 
        }
        QLabel.searchStatus {
            color: #666;
            font-size: 12px;
            background: transparent;
            padding-right: 5px;
        } 
                        
        /*Кнопки общий стиль*/
        QPushButton.norm {
            background-color: #5c7080;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-size: 14px;
            min-width: 100px;
        }
        QPushButton.norm:hover {
            background-color: #6f8dab;
        }
        QPushButton.norm:pressed {
            background-color: #4a5d6c;
        }
        QPushButton.norm:disabled {
            background-color: #cccccc;
            color: #666666;
        }  
        /*Кнопки крит*/
        QPushButton.crit {
            background-color: #c44d58;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-size: 14px;
            min-width: 100px;
        }
        QPushButton.crit:hover {
            background-color: #d66e78;
        }
        QPushButton.crit:pressed {
            background-color: #a33d47;
        }
        QPushButton.crit:disabled {
            background-color: #cccccc;
            color: #666666;
        }
                        
        /*Кнопки поиска*/
        QPushButton.navi {
            background-color: #5c7080;
            color: white;
            border: none;
            padding: 0;
            border-radius: 4px;
            font-size: 14px;
            min-width: 30px;
            max-width: 30px;
            height:34px;
            width:34px;
            margin-right:6px;
        }
        QPushButton.navi:hover {
            background-color: #6f8dab;
        }
        QPushButton.navi:pressed {
            background-color: #4a5d6c;
        }
        QPushButton.navi:disabled {
            background-color: #cccccc;
            color: #666666;
        }
    """

    @staticmethod
    def apply_theme(theme_name):
        app = QApplication.instance()
        
        if theme_name == "light":
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(240, 242, 245))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.Text, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(92, 112, 128))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
            app.setPalette(palette)
            # Добавляем цвета для подсветки
            app.setProperty("highlight_color", QColor(65, 105, 225))  # RoyalBlue для светлой темы
            app.setProperty("error_color", QColor(220, 20, 60))      # Crimson для светлой темы
            
            # Стили для светлой темы
            theme_style = """
                QLineEdit {
                    color: #333333;
                    background-color: white;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 0px;
                }
                QTableWidget {
                    gridline-color: #e0e0e0;
                    font-size: 12pt;
                }
                QHeaderView::section {
                    background-color: #f0f0f0;
                    padding: 4px;
                    border: 1px solid #d0d0d0;
                    font-weight: bold;
                }
                QTableWidget::item {
                    padding: 6px;
                    border-bottom: 1px solid #e0e0e0;
                }
                QStatusBar {
                    background-color: #f0f0f0;
                    border-top: 1px solid #d0d0d0;
                    padding: 2px;
                }
                QStatusBar QLabel {
                    color: #333;
                    padding: 0 5px;
                }
            """
            
        elif theme_name == "dark":
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(66, 66, 66))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.Text, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.Button, QColor(66, 66, 66))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(240, 240, 240))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(111, 141, 171))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
            app.setPalette(palette)
            # Добавляем цвета для подсветки
            app.setProperty("highlight_color", QColor(100, 149, 237))  # CornflowerBlue для темной темы
            app.setProperty("error_color", QColor(255, 69, 0))        # OrangeRed для темной темы


            # Стили для темной темы
            theme_style ="""
        
                QLineEdit {
                     color: #e0e0e0;
                     background-color: #424242;
                     border: 1px solid #555;
                     border-radius: 4px;
                     padding: 0px;
                 }
                QTableWidget {
                    gridline-color: #444;
                    font-size: 12pt;
                }
                QHeaderView::section {
                    background-color: #444;
                    color: #eee;
                    padding: 4px;
                    border: 1px solid #555;
                    font-weight: bold;
                }
                QTableWidget::item {
                    padding: 4px;
                    border-bottom: 1px solid #444;
                }
                QStatusBar {
                    background-color: #333;
                    border-top: 1px solid #555;
                    padding: 2px;
                }
                QStatusBar QLabel {
                    color: #e0e0e0;
                    padding: 0 5px;
                }
            """
        full_style = ThemeManager.COMMON_STYLE + theme_style
        app.setStyleSheet(full_style)


class DeleteInactiveDialog(QDialog):
    def __init__(self, config_flags, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Удаление неактивных станций")
        self.setFixedSize(300, 200)
        
        layout = QVBoxLayout()
        
        # Текстовое сообщение
        layout.addWidget(QLabel("Выберите флаги для удаления:"))
        
        # Список флагов с чекбоксами
        self.flags_layout = QVBoxLayout()
        self.checkboxes = {}
        flags = [
            ("404", "HTTP 404 (Страница не найдена)"),
            ("Error", "Любая ошибка сервера"), 
            ("ConnError", "Ошибка соединения"),
            ("Timeout", "Таймаут запроса")
        ]
        
        # Создаем чекбоксы для каждого флага
        for flag_key, flag_label in flags:
            checkbox = QCheckBox(flag_label)
            checkbox.setChecked(config_flags.get(flag_key, True))  # По умолчанию включены
            self.checkboxes[flag_key] = checkbox
            self.flags_layout.addWidget(checkbox)
        
        layout.addLayout(self.flags_layout)
        layout.addSpacing(10)
        
        # Сообщение подтверждения
        layout.addWidget(QLabel("Удалить неактивные станции?"))
        
        # Кнопки
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_selected_flags(self):
        """Возвращает список выбранных флагов в формате [Тип]"""
        flags = []
        for flag_key, checkbox in self.checkboxes.items():
            if checkbox.isChecked():
                flags.append(f"[{flag_key}]")
        return flags
    

class SettingsDialog(QDialog):
    def __init__(self, current_theme, max_threads, current_timeout, current_rename_template, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setFixedSize(400, 450)
        
        layout = QVBoxLayout()
        
        # Выбор темы
        layout.addWidget(QLabel("Тема оформления:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Светлая", "light")
        self.theme_combo.addItem("Темная", "dark")
        
        # Устанавливаем текущую тему
        index = self.theme_combo.findData(current_theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
            
        layout.addWidget(self.theme_combo)
        
        # Количество потоков для проверки
        layout.addWidget(QLabel("Потоков для проверки станций:"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 50)
        self.threads_spin.setValue(max_threads)
        layout.addWidget(self.threads_spin)
        
        # Таймаут проверки
        layout.addWidget(QLabel("Таймаут проверки (сек.):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 60)
        self.timeout_spin.setValue(current_timeout)
        self.timeout_spin.setSuffix(" сек")
        layout.addWidget(self.timeout_spin)

        # Шаблон переименования 
        layout.addWidget(QLabel("Шаблон \"Фикса Названий\":"))
        self.rename_template_edit = QTextEdit()
        self.rename_template_edit.setMaximumHeight(80)
        self.rename_template_edit.setPlainText(current_rename_template)
        layout.addWidget(self.rename_template_edit)

        # Кнопки вставки переменных в настройках
        layout.addWidget(QLabel("Вставить переменную:"))
        variables_layout = QHBoxLayout()
        for var in RENAME_VARIABLES:
            btn = QPushButton(var)
            # Очень важно зафиксировать значение 'var' в лямбда-выражении!
            btn.clicked.connect(lambda checked, v=var: self.insert_variable_to_settings(v))
            variables_layout.addWidget(btn)
        variables_layout.addStretch()
        layout.addLayout(variables_layout)

        # Кнопки
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)

    def insert_variable_to_settings(self, variable):
        """Вставить переменную в поле шаблона настроек"""
        cursor = self.rename_template_edit.textCursor()
        cursor.insertText(variable)
        self.rename_template_edit.setFocus()

    def get_selected_theme(self):
        return self.theme_combo.currentData()
    
    def get_max_threads(self):
        return self.threads_spin.value()
    
    def get_timeout(self):
        return self.timeout_spin.value()

    def get_rename_template(self):
        return self.rename_template_edit.toPlainText().strip()


class RenameTemplateDialog(QDialog):
    def __init__(self, current_template, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Фикс Названий - Шаблон")
        self.setFixedSize(500, 350)
        
        layout = QVBoxLayout()
        
        # Предупреждающий текст
        warning_label = QLabel(
            "Внимание! Это действие изменит названия активных станций.\n"
            "Используйте переменные для задания шаблона."
        )
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)
        
        # Поле шаблона
        layout.addWidget(QLabel("Шаблон переименования:"))
        self.template_edit = QTextEdit()
        self.template_edit.setPlainText(current_template)
        self.template_edit.setMaximumHeight(100)
        layout.addWidget(self.template_edit)
        
        # Кнопки вставки переменных
        layout.addWidget(QLabel("Вставить переменную:"))
        variables_layout = QHBoxLayout()
        for var in RENAME_VARIABLES:
            btn = QPushButton(var)
            btn.clicked.connect(lambda checked, v=var: self.insert_variable(v))
            variables_layout.addWidget(btn)
        variables_layout.addStretch()
        layout.addLayout(variables_layout)
        
        # Кнопки выбора действия
        buttons_layout = QHBoxLayout()
        self.all_btn = QPushButton("Все")
        self.selected_btn = QPushButton("Выбранный")
        self.cancel_btn = QPushButton("Отмена")
        
        self.all_btn.clicked.connect(lambda: self.done(1)) # Вернём 1
        self.selected_btn.clicked.connect(lambda: self.done(2)) # Вернём 2
        self.cancel_btn.clicked.connect(self.reject) # Вернём QDialog.Rejected
        
        buttons_layout.addWidget(self.all_btn)
        buttons_layout.addWidget(self.selected_btn)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.cancel_btn)
        layout.addLayout(buttons_layout)
        
        self.setLayout(layout)

    def insert_variable(self, variable):
        """Вставить переменную в текущую позицию курсора"""
        cursor = self.template_edit.textCursor()
        cursor.insertText(variable)
        self.template_edit.setFocus()

    def get_template(self):
        """Получить текущий шаблон из текстового поля"""
        return self.template_edit.toPlainText().strip()
    

class EditDialog(QDialog):
    def __init__(self, row_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактирование записи")
        self.setFixedSize(400, 300)
        
        layout = QVBoxLayout()
        
        # Название
        layout.addWidget(QLabel("Название станции:"))
        self.name_edit = QLineEdit(row_data[0])
        layout.addWidget(self.name_edit)
        
        # Адрес (большое поле)
        layout.addWidget(QLabel("Адрес станции:"))
        self.address_edit = QTextEdit(row_data[1])
        self.address_edit.setAcceptRichText(False)
        self.address_edit.setMinimumHeight(100)
        layout.addWidget(self.address_edit)
        
        # Volume
        layout.addWidget(QLabel("Volume:"))
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(-64, 64)
        self.volume_spin.setValue(int(row_data[2]))
        layout.addWidget(self.volume_spin)
        
        # Кнопки
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_data(self):
        return [
            self.name_edit.text(),
            self.address_edit.toPlainText(),
            str(self.volume_spin.value())
        ]


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справка - Radio Manager")
        self.setFixedSize(600, 700)
        
        layout = QVBoxLayout()
        
        # Заголовок
        title_label = QLabel("Radio Manager v1.0.0")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("""
            QLabel {
                font-size: 24px;
                font-weight: bold;
                color: #5c7080;
                margin: 10px;
            }
        """)
        layout.addWidget(title_label)
        
        # Описание
        description_label = QLabel("Приложение для управления плейлистами интернет-радиостанций")
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                color: #666;
                margin-bottom: 20px;
            }
        """)
        layout.addWidget(description_label)
        
        # Основной текст справки с встроенными стилями
        help_text = """
        <style>
            h2 {
                color: #5c7080;
                border-bottom: 1px solid #e0e0e0;
                padding-bottom: 5px;
                margin-top: 20px;
            }
            ul {
                margin-top: 10px;
                margin-bottom: 15px;
            }
            li {
                margin-bottom: 5px;
            }
            a {
                color: #5c7080;
                text-decoration: none;
                text-decoration: underline;

            }
            a:hover {
                color: #6f8dab;
                text-decoration: underline;
            }
            p{
                font-size:16px;
            }
        </style>
        
        <h2>Возможности:</h2>
        <ul>
            <li>Проверка файла CSV при открытии, устранение проблем синтаксиса.</li>
            <li>Многопоточная проверка станций на существование</li>
            <li>Сбор информации о станциях (название, контейнер, качество потока).</li>
            <li>Удаление неактивных станций.</li>
            <li>Массовое переименование станций по шаблонам.</li>
            <li>Живой поиск станций по имени, адресу, или информации.</li>
            <li>Поиск и удаление дубликатов станций по адресам.</li>
            <li>Массовое переименование протоколов (https->http).</li>
            <li>Редактирование станции через окно редактора, или по щелчку на ячейку таблицы.</li>
            <li>Сортировка плейлиста мышью.</li>
            <li>Интерактивный обмен станциями между плейлистами (копиями программы).</li>
            <li>Прослушивание потоков в приложении.</li>
            <li>Светлая и темная тема оформления.</li>
            <li>Сохранение плейлиста в формате CSV для ёрадио.</li>
        </ul>
        
        <h2><a href="https://github.com/leowerd/radio-manager/blob/main/README.md#%D1%81%D0%BF%D1%80%D0%B0%D0%B2%D0%BA%D0%B0">Мануал с картинками</a></h2>
        <p>Скачать свежую версию, или сообщить об ошибке можно на <a href="https://github.com/leowerd/radio-manager/"><b>гитхабе</b></a>.</p>
        
        <p><b>Автор: werdes</b></p>
        """
        
        help_browser = QTextBrowser()
        help_browser.setOpenExternalLinks(True)
        help_browser.setHtml(help_text)
        
        # Установка стилей для текстового браузера
        help_browser.setStyleSheet("""
            QTextBrowser {
                border: none;
                background: transparent;
                font-size: 13px;
                selection-background-color: #5c7080;
                selection-color: white;
            }
        """)
        
        layout.addWidget(help_browser)
        
        # Кнопка закрытия
        close_button = QPushButton("Закрыть")
        close_button.setProperty("class", "norm")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        
        self.setLayout(layout)

class IntegerValidatorDelegate(QStyledItemDelegate):
    """Делегат для валидации целых чисел"""
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setValidator(QIntValidator(-64, 64, editor))  # Ограничение 
        return editor

    def setEditorData(self, editor, index):
        value = index.model().data(index, Qt.ItemDataRole.EditRole)
        editor.setText(str(value))

    def setModelData(self, editor, model, index):
        value = editor.text()
        model.setData(index, value, Qt.ItemDataRole.EditRole)


class UIStateManager(QObject):
    def update_state(self):
        """Обновляет состояние UI на основе текущих флагов"""
        base_enabled = not self.is_checking
        
        self.main.open_csv_btn.setEnabled(base_enabled)
        self.main.save_csv_btn.setEnabled(base_enabled and self.has_data)
        self.main.add_btn.setEnabled(base_enabled)
        self.main.edit_btn.setEnabled(base_enabled and self.row_selected)
        self.main.del_btn.setEnabled(base_enabled and self.row_selected)
        self.main.find_duplicates_btn.setEnabled(base_enabled and self.has_data)
        self.main.find_inactive_btn.setEnabled(self.has_data)
        self.main.fix_https_btn.setEnabled(base_enabled and self.has_data)
        self.main.player_btn.setEnabled(base_enabled and self.row_selected)
        self.main.settings_btn.setEnabled(base_enabled)
        self.main.search_edit.setEnabled(base_enabled)
        
        # Новые состояния кнопок удаления
        self.main.remove_duplicates_btn.setEnabled(
            base_enabled and self.has_duplicates
        )
        self.main.remove_inactive_btn.setEnabled(
            base_enabled and (self.check_completed or (self.is_checking and self.found_inactive))
        )
        
        # Кнопка "Фикс названий"
        self.main.fix_names_btn.setEnabled(base_enabled and self.check_completed)
    
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        
        # Инициализация состояний
        self.has_data = False
        self.row_selected = False
        self.is_checking = False
        self.check_completed = False
        self.has_duplicates = False
        self.found_inactive = False  # Новый флаг: найдены ли битые станции
        
        self.update_state()



class TableWidgetWithDrag(QTableWidget):
    # Сигнал для уведомления об изменении количества строк
    row_count_changed = pyqtSignal(int)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Настройки основного виджета
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        
        # Разрешаем оба формата перетаскивания - внутреннее и внешнее
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        
        # Настраиваем перемещение строк через заголовок (внутри приложения)
        self.verticalHeader().setSectionsMovable(True)
        self.verticalHeader().setDragEnabled(True)
        self.verticalHeader().setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        
        # Подключаем обработчик перемещения внутренних строк
        self.verticalHeader().sectionMoved.connect(self.recreateRowsAfterMove)
    
    # Захват данных для перетаскивания
    def startDrag(self, supportedActions):
        # Проверяем наличие выбранных элементов
        selected_items = self.selectedItems()
        if not selected_items:
            return
            
        # Находим строку первого выбранного элемента
        row = self.row(selected_items[0])
        if row < 0:
            return
            
        # Собираем данные выбранной строки
        row_data = []
        for col in range(self.columnCount()):
            item = self.item(row, col)
            row_data.append(item.text() if item else "")
        
        # Создаем MIME-объект с данными таблицы в формате JSON
        mime_data = QMimeData()
        json_data = json.dumps(row_data, ensure_ascii=False)
        mime_data.setData(
            "application/json", 
            QByteArray(json_data.encode('utf-8'))
        )
        
        # Добавляем обычный текст для совместимости в виде CSV (на всякий случай)
        mime_data.setText("\t".join(row_data))
        
        # Создаем объект перетаскивания
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        
        # Параметры выполняемой операции
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

    # Проверка возможности приема перетаскивания
    def dragEnterEvent(self, event):
        # Проверяем наличие наших данных в MIME
        if (event.mimeData().hasFormat("application/json") or 
            event.mimeData().hasText()):
            event.acceptProposedAction()
        else:
            event.ignore()

    # Реакция на движение курсора при перетаскивании
    def dragMoveEvent(self, event):
        if (event.mimeData().hasFormat("application/json") or 
            event.mimeData().hasText()):
            event.accept()
        else:
            event.ignore()

    # Обработка события "бросания" объекта
    def dropEvent(self, event):
        # Используем наш кастомный обработчик для перетаскивания из другого приложения
        if event.source() != self:
            self.handleExternalDrop(event)
            event.accept()
        else:
            # Для внутреннего перетаскивания игнорируем событие
            event.ignore()
    
    def handleExternalDrop(self, event):
        """Обработка перетаскивания из других приложений"""
        mime_data = event.mimeData()
        row_data = []
        
        # Определяем позицию курсора для вставки
        pos = event.position().toPoint() if hasattr(event.position(), 'toPoint') else event.pos()
        drop_row = self.indexAt(pos).row()
        
        # Если курсор между строк или ниже последней строки
        if drop_row < 0:
            drop_row = self.rowCount()
        
        # Пробуем получить данные в формате JSON
        if mime_data.hasFormat("application/json"):
            byte_data = mime_data.data("application/json").data()
            try:
                row_data = json.loads(byte_data.decode('utf-8'))
            except:
                pass
        
        # Если не получилось JSON, пробуем получить как текст
        if not row_data and mime_data.hasText():
            text = mime_data.text()
            if "\t" in text:  # Tab-delimited
                row_data = text.split("\t")
            elif "," in text:  # CSV
                row_data = text.split(",")
            else:  # Используем всю строку как первый столбец
                row_data = [text]
        
        if row_data:
            # Дообираем недостающие данные
            while len(row_data) < 4:
                row_data.append("")
            
            # Вставляем новую строку в указанную позицию
            self.insertRow(drop_row)
            
            # Заполняем данные столбцов
            for col in range(min(len(row_data), 4)):
                text = str(row_data[col]).strip()
                item = QTableWidgetItem(text)
                
                # Устанавливаем флаги редактирования
                if col in (0, 1, 2):  # Разрешаем редактирование только для первых трех столбцов
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                
                # Сохраняем выравнивание для Volume
                if col == 2:
                    try:
                        # Проверяем, является ли число
                        int(text)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    except ValueError:
                        pass
                
                self.setItem(drop_row, col, item)
            
            # Выделяем добавленную строку
            self.selectRow(drop_row)
            # Отправляем сигнал об изменении количества строк
            self.row_count_changed.emit(self.rowCount())


    def recreateRowsAfterMove(self, logicalIndex, oldIndex, newIndex):
        """Сортировка строк после перемещения"""
        # Вычисляем реальные позиции строк
        srcRow = oldIndex
        dstRow = newIndex
        
        if srcRow == dstRow:
            return
            
        # Сохраняем данные строки-источника
        row_data = []
        for col in range(self.columnCount()):
            item = self.takeItem(srcRow, col)
            row_data.append(item)
        
        # Удаляем исходную строку
        self.removeRow(srcRow)
        
        # Вставляем новую строку в нужную позицию
        self.insertRow(dstRow)
        for col in range(self.columnCount()):
            self.setItem(dstRow, col, row_data[col])
        
        # Выделяем перемещенную строку
        self.selectRow(dstRow)
        
        # Блокируем рекурсивные вызовы
        self.verticalHeader().sectionMoved.disconnect(self.recreateRowsAfterMove)
        self.verticalHeader().moveSection(dstRow, dstRow)
        self.verticalHeader().sectionMoved.connect(self.recreateRowsAfterMove)
        # Отправляем сигнал об изменении количества строк
        self.row_count_changed.emit(self.rowCount())


    def highlight_row(self, row, state="default"):
        """Подсветка строки в зависимости от состояния"""
        if row < 0 or row >= self.rowCount():
            return
            
        color_map = {
            "default": None,  # Вернет стандартный цвет
            "highlight": QApplication.instance().property("highlight_color"),
            "error": QApplication.instance().property("error_color")
        }
        
        color = color_map.get(state.lower())
        
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                if color:
                    item.setForeground(color)
                else:
                    # Возвращаем стандартный цвет текста
                    palette = QApplication.instance().palette()
                    item.setForeground(palette.color(QPalette.ColorRole.Text))
    
    def reset_all_highlighting(self):
        """Сбросить всю подсветку к стандартным цветам"""
        for row in range(self.rowCount()):
            self.highlight_row(row, "default")


class StreamPlayer(QObject):

    # Словарь для преобразования MIME-типов в читаемые названия
    FORMAT_MAPPING = {
        'audio/mpeg': 'MP3',
        'audio/aac': 'AAC',
        'audio/aacp': 'AAC+',
        'audio/mp4': 'MP4',
        'audio/flac': 'FLAC',
        'audio/ogg': 'OGG',
        'audio/wav': 'WAV',
        'audio/x-wav': 'WAV',
        'audio/vnd.wav': 'WAV',
        'audio/x-mpegurl': 'M3U',
        'audio/scpls': 'PLS',
        'application/vnd.apple.mpegurl': 'M3U8',
        'application/x-mpegurl': 'M3U',
        'application/pls+xml': 'PLS',
        'application/xspf+xml': 'XSPF'
    }
    
    # Сигналы для обновления UI
    info_updated = pyqtSignal(str, str, str)  # станция, трек, формат/битрейт
    playback_toggled = pyqtSignal(bool)       # состояние воспроизведения
    
    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        # Текущее состояние
        self.current_url = None
        self.current_row = -1
        self.is_playing = False
        self.info_timer = None
        
        # Подключаем сигналы
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
    
    def toggle_playback(self, row, url):
        """Переключение воспроизведения"""
        # Если та же станция или нет станции - останавливаем
        if (row == self.current_row and self.is_playing) or row < 0:
            self.stop()
            return False
        else:
            # Играем новую станцию
            self.play(row, url)
            return True
    
    def play(self, row, url):
        """Начать воспроизведение"""
        self.stop()  # Останавливаем текущее воспроизведение
        
        self.current_row = row
        self.current_url = url
        self.player.setSource(QUrl(url))
        self.player.play()
        self.is_playing = True
        self.playback_toggled.emit(True)
        
        # Запрашиваем информацию о потоке
        self._update_stream_info()
        
        # Запускаем таймер для обновления информации
        self.info_timer = QTimer()
        self.info_timer.timeout.connect(self._update_stream_info)
        self.info_timer.start(5000)  # Каждые 5 секунд
    
    def stop(self):
        """Остановить воспроизведение"""
        if self.info_timer:
            self.info_timer.stop()
            self.info_timer = None
            
        self.player.stop()
        self.is_playing = False
        self.current_url = None
        self.current_row = -1
        self.playback_toggled.emit(False)
    
    def set_volume(self, volume):
        """Установить громкость (0.0 - 1.0) и сохранить в настройках"""
        self.audio_output.setVolume(volume)
        # Сохраняем в настройках
        config = self.config_manager.load_config()
        config['Settings']['player_volume'] = str(volume)
        self.config_manager.save_config(config)
    
    def get_saved_volume(self):
        """Получить сохраненную громкость"""
        config = self.config_manager.load_config()
        return float(config['Settings'].get('player_volume', '0.5'))
    
    def _normalize_format(self, content_type):
        """Преобразование MIME-типа в читаемый формат"""
        if not content_type or content_type == 'Неизвестно':
            return 'Неизвестно'
        
        # Приводим к нижнему регистру для сравнения
        content_type = content_type.lower().strip()
        
        # Используем словарь для преобразования
        if content_type in self.FORMAT_MAPPING:
            return self.FORMAT_MAPPING[content_type]
        
        # Если тип не найден в словаре, пытаемся извлечь подтип
        if '/' in content_type:
            subtype = content_type.split('/')[-1].upper()
            # Убираем лишние параметры (например, "mpeg; charset=utf-8" -> "mpeg")
            subtype = subtype.split(';')[0].strip()
            return subtype
        
        # Если ничего не помогло, возвращаем как есть
        return content_type.upper()


    def _update_stream_info(self):
        """Обновить информацию о потоке, используя потоковое соединение"""
        if not self.current_url:
            return

        try:
            url = QUrl(self.current_url)
            host = url.host()
            path = url.path()
            port = url.port(80)  # По умолчанию порт 80, если не указан

            # Создать сокет
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(5)  # Установить таймаут 5 секунд

            # Подключиться к серверу
            client_socket.connect((host, port))

            # Отправить HTTP-запрос с Icy-MetaData: 1
            request = f"GET {path} HTTP/1.0\r\n" \
                      f"Host: {host}\r\n" \
                      f"Icy-MetaData: 1\r\n" \
                      f"User-Agent: Mozilla/5.0\r\n\r\n"
            client_socket.sendall(request.encode())

            # Получить ответ
            response = client_socket.recv(4096).decode('utf-8', errors='ignore')

            # Разобрать заголовки
            headers = {}
            for line in response.split('\r\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()

            # Извлечь метаданные
            station_name = headers.get('icy-name', 'Неизвестно')
            genre = headers.get('icy-genre', 'Неизвестно')
            bitrate = headers.get('icy-br', 'Неизвестно')
            content_type = headers.get('content-type', 'Неизвестно')
            icy_metaint = headers.get('icy-metaint', '0')

            # Попытаться получить название потока, если доступен metainterval
            stream_title = 'Неизвестно'
            metaint = int(icy_metaint)
            if metaint > 0:
                # Прочитать поток, чтобы получить блок метаданных
                client_socket.settimeout(2)  # Короткий таймаут для метаданных
                client_socket.recv(metaint)  # Отбросить данные, не являющиеся метаданными
                metadata_length = ord(client_socket.recv(1)) * 16  # Длина в 16 байтах
                metadata = client_socket.recv(metadata_length).decode('utf-8', errors='ignore')
                match = re.search(r"StreamTitle='([^']*)';", metadata)
                if match:
                    stream_title = match.group(1)
            
            # Закрыть сокет
            client_socket.close()
            
            # Нормализовать формат
            normalized_format = self._normalize_format(content_type)

            # Сформировать строку информации
            if bitrate != 'Неизвестно':
                format_info = f"{normalized_format} / {bitrate} kbps"
            else:
                format_info = normalized_format
            
            # Отправить сигнал с обновленной информацией
            self.info_updated.emit(station_name, stream_title, format_info)


        except Exception as e:
            self.info_updated.emit("Ошибка", str(e), "Неизвестно")

    
    def _on_playback_state_changed(self, state):
        """Обработчик изменения состояния воспроизведения"""
        pass
    
    def is_currently_playing(self):
        """Проверить, играет ли сейчас что-то"""
        return self.is_playing
    
    def get_current_row(self):
        """Получить текущую строку"""
        return self.current_row


class PlaylistParser:
    """Парсер плейлистов: M3U/M3U8, PLS, XSPF."""
    MAX_DEPTH = 3  # ограничение вложенности

    @staticmethod
    def fetch_and_parse(url, depth=0, visited=None):
        """
        Загружает и парсит плейлист по URL. Возвращает [{'url':..., 'title':...}, ...].
        """
        from urllib.parse import urljoin
        import xml.etree.ElementTree as ET

        if visited is None:
            visited = set()

        if depth > PlaylistParser.MAX_DEPTH:
            return []
        if url in visited:
            return []

        visited.add(url)

        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Icy-MetaData': '1', 'Connection': 'close'}
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code != 200:
                return []

            text = r.text
            s = text.lstrip().lower()

            # M3U
            if "#extm3u" in s or "#extinf" in s:
                return PlaylistParser._parse_m3u(text, url)
            # PLS
            if "[playlist]" in s or "file1=" in s:
                return PlaylistParser._parse_pls(text, url)
            # XSPF
            if "<playlist" in s and "<tracklist" in s:
                return PlaylistParser._parse_xspf(text, url)

            # Fallback: просто ссылки
            entries = []
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and line.lower().startswith("http"):
                    entries.append({"url": urljoin(url, line), "title": None})
            return entries
        except Exception:
            return []

    @staticmethod
    def _parse_m3u(content, base_url):
        from urllib.parse import urljoin
        entries = []
        pending_title = None
        for ln in content.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if ln.upper().startswith('#EXTINF'):
                parts = ln.split(',', 1)
                if len(parts) == 2:
                    pending_title = parts[1].strip()
                continue
            if ln.startswith('#'):
                continue
            entries.append({'url': urljoin(base_url, ln), 'title': pending_title})
            pending_title = None
        return entries

    @staticmethod
    def _parse_pls(content, base_url):
        from urllib.parse import urljoin
        entries = []
        file_map = {}
        title_map = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip().lower()
            v = v.strip()
            if k.startswith('file'):
                try:
                    idx = int(k.replace('file', ''))
                    file_map[idx] = v
                except:
                    pass
            elif k.startswith('title'):
                try:
                    idx = int(k.replace('title', ''))
                    title_map[idx] = v
                except:
                    pass
        for idx in sorted(file_map.keys()):
            entries.append({'url': urljoin(base_url, file_map[idx]), 'title': title_map.get(idx)})
        return entries

    @staticmethod
    def _parse_xspf(content, base_url):
        import xml.etree.ElementTree as ET
        from urllib.parse import urljoin
        entries = []
        try:
            root = ET.fromstring(content.encode('utf-8'))
            for track in root.iter():
                if track.tag.lower().endswith('track'):
                    loc = None
                    title = None
                    for ch in track:
                        tag = ch.tag.lower()
                        if tag.endswith('location'):
                            loc = ch.text.strip() if ch.text else None
                        elif tag.endswith('title'):
                            title = ch.text.strip() if ch.text else None
                    if loc:
                        entries.append({'url': urljoin(base_url, loc), 'title': title})
        except Exception:
            pass
        return entries


class StationChecker(QObject):
    # Сигналы для обновления UI
    progress_updated = pyqtSignal(int, int)  # проверено, всего
    station_checked = pyqtSignal(int, str)   # строка, информация
    check_finished = pyqtSignal(int, int, int)  # проверено, активных, мертвых
    check_cancelled = pyqtSignal()
    
    def __init__(self, max_threads=10, timeout=10):
        super().__init__()
        self.max_threads = max_threads
        self.timeout = timeout  # Таймаут в секундах
        self.cancel_flag = False
        self.threads = []
    

    def _normalize_format(self, content_type):
        """Преобразование MIME-типа в читаемый формат"""
        FORMAT_MAPPING = {
            'audio/mpeg': 'MP3',
            'audio/aac': 'AAC',
            'audio/aacp': 'AAC+',
            'audio/mp4': 'MP4',
            'audio/flac': 'FLAC',
            'audio/ogg': 'OGG',
            'audio/wav': 'WAV',
            'audio/x-wav': 'WAV',
            'audio/vnd.wav': 'WAV',
            'audio/x-mpegurl': 'M3U',
            'audio/scpls': 'PLS',
            'application/vnd.apple.mpegurl': 'M3U8',
            'application/x-mpegurl': 'M3U',
            'application/pls+xml': 'PLS',
            'application/xspf+xml': 'XSPF'
        }
        
        if not content_type or content_type == 'Неизвестно':
            return 'Неизвестно'
        
        # Приводим к нижнему регистру для сравнения
        content_type = content_type.lower().strip()
        
        # Используем словарь для преобразования
        if content_type in FORMAT_MAPPING:
            return FORMAT_MAPPING[content_type]
        
        # Если тип не найден в словаре, пытаемся извлечь подтип
        if '/' in content_type:
            subtype = content_type.split('/')[-1].upper()
            # Убираем лишние параметры
            subtype = subtype.split(';')[0].strip()
            return subtype
        
        # Если ничего не помогло, возвращаем как есть
        return content_type.upper()


    def get_timeout(self):
        """Получить текущий таймаут"""
        return self.timeout

    def set_timeout(self, timeout):
        """Установить таймаут для проверки"""
        self.timeout = timeout
    
    def fix_icy_encoding(self, text):
        """Исправление кодировки ICY данных с помощью charset-normalizer"""
        if text is None or text == 'Неизвестно':
            return text
        
        try:
            # Если текст выглядит нормально (нет явных признаков битой кодировки)
            if not self._has_encoding_issues(text):
                return text
            
            # Преобразуем строку в байты
            if isinstance(text, str):
                # Получаем байты как latin-1 (чтобы получить оригинальные байты)
                text_bytes = text.encode('latin-1')
                
                # Используем charset-normalizer для определения кодировки
                # Это более точный и современный метод чем chardet
                results = charset_normalizer.from_bytes(text_bytes)
                
                if results:
                    # Берем самый вероятный результат
                    best_result = results.best()
                    if best_result:
                        decoded_text = str(best_result)
                        # Проверяем, что результат выглядит разумно
                        if self._is_text_valid(decoded_text):
                            return decoded_text
                
                # Если charset-normalizer не помог, пробуем популярные кодировки вручную
                fallback_encodings = ['cp1251', 'koi8-r', 'iso-8859-5', 'cp866', 'utf-8']
                for enc in fallback_encodings:
                    try:
                        decoded_text = text_bytes.decode(enc)
                        if self._is_text_valid(decoded_text):
                            return decoded_text
                    except:
                        continue
                        
            return text  # Если ничего не помогло, возвращаем как есть
            
        except Exception as e:
            # print(f"Encoding fix error: {e}")  # Для отладки
            return text  # Возвращаем как есть в случае ошибок
    
    def _has_encoding_issues(self, text):
        """Проверяет наличие признаков проблем с кодировкой"""
        if not isinstance(text, str):
            return False
        # Типичные признаки битой UTF-8 в кодировках типа cp1251
        return any(char in text for char in [
            'Ð', 'Ñ', 'Â', '', '', '', '', '', '', '', '', 
            '', '', '', '', '', '', '', ''
        ])
    
    def _is_text_valid(self, text):
        """Проверяет, что декодированный текст выглядит адекватно"""
        if not text:
            return False
            
        # Считаем "странные" символы
        strange_chars = 0
        total_chars = 0
        
        for c in text:
            total_chars += 1
            # Проверяем на "странные" символы (не ASCII, не кириллица, не знаки препинания)
            if ord(c) > 127:
                if not ('\u0400' <= c <= '\u04FF' or  # Кириллица
                        '\u00C0' <= c <= '\u017F' or  # Латинские дополнения  
                        c in ' «»—–№ёЁ†‡‰Љ‹ЊЋЏ'):  # Распространенные символы
                    strange_chars += 1
        
        # Если больше 30% странных символов - считаем недействительным
        return (strange_chars / total_chars) < 0.3 if total_chars > 0 else True
    
    def _is_playlist(self, url, content_type):
        """Определяет, является ли контент плейлистом"""
        if any(ext in url for ext in ['.m3u', '.m3u8', '.pls', '.xspf']):
            return True
        content_type = content_type.lower()
        return any(key in content_type for key in [
            'm3u', 'mpegurl', 'playlist', 'audio/x-mpegurl', 
            'application/vnd.apple.mpegurl', 'audio/scpls',
            'application/xspf+xml'
        ])
    
    def _is_html_response(self, content, content_type):
        """Определяет, является ли ответ HTML-страницей"""
        content_type = content_type.lower()
        if 'text/html' in content_type:
            return True
        
        # Проверяем первые 100 символов на наличие HTML-тегов
        sample = content[:100].lower()
        return any(tag in sample for tag in ['<html', '<!doctype', '<body', '<head', '<title'])
    
    def check_stations(self, stations_data):
        """Запустить проверку станций"""
        self.cancel_flag = False
        self.threads = []
        
        total_stations = len(stations_data)
        checked_count = 0
        active_count = 0
        dead_count = 0
        
        # Используем семафор для ограничения количества потоков
        semaphore = threading.Semaphore(self.max_threads)
        
        def check_station(row, url):
            nonlocal checked_count, active_count, dead_count
            
            if self.cancel_flag:
                semaphore.release()
                return
                
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Icy-MetaData': '1',
                    'Connection': 'close'
                }
                
                # Сначала пробуем HEAD для быстрой проверки
                try:
                    redirect_response = requests.head(url, headers=headers, allow_redirects=True, timeout=5)
                    final_url = redirect_response.url
                    response = requests.get(final_url, headers=headers, stream=True, timeout=self.timeout)
                except:
                    # Если HEAD не работает, сразу пробуем GET
                    response = requests.get(url, headers=headers, stream=True, timeout=self.timeout)
                
                if response.status_code == 200:
                    content_type = response.headers.get('content-type', 'Неизвестно').lower()
                    content_sample = b''
                    
                    # Проверяем, не является ли ответ HTML-страницей
                    for chunk in response.iter_content(1024):
                        content_sample += chunk
                        if len(content_sample) > 100:
                            break
                    
                    if self._is_html_response(content_sample.decode('latin-1', errors='ignore'), content_type):
                        info = "[404]"
                        dead_count += 1
                    else:
                        # Проверяем, является ли контент плейлистом
                        if self._is_playlist(response.url, content_type):
                            try:
                                playlist_entries = PlaylistParser.fetch_and_parse(response.url)
                                if playlist_entries:
                                    # Получаем метаданные с исправлением кодировки
                                    station_name = self.fix_icy_encoding(response.headers.get('icy-name'))
                                    genre = self.fix_icy_encoding(response.headers.get('icy-genre'))
                                    bitrate = response.headers.get('icy-br', 'Неизвестно')
                                    content_type = response.headers.get('content-type', 'Неизвестно')

                                    # Нормализуем формат
                                    content_type= self._normalize_format(content_type)

                                    # Если значения None, заменяем на 'Неизвестно'
                                    station_name = station_name if station_name else 'Неизвестно'
                                    genre = genre if genre else 'Неизвестно'
                                    
                                    info = f"[OK][PL: {len(playlist_entries)}][{station_name}][{content_type}][{bitrate}][{genre}]"
                                    active_count += 1
                                else:
                                    info = "[Error]"
                                    dead_count += 1
                            except Exception as e:
                                info = "[Error]"
                                dead_count += 1
                        else:
                            # Получаем метаданные с исправлением кодировки
                            station_name = self.fix_icy_encoding(response.headers.get('icy-name'))
                            genre = self.fix_icy_encoding(response.headers.get('icy-genre'))
                            bitrate = response.headers.get('icy-br', 'Неизвестно')
                            content_type = response.headers.get('content-type', 'Неизвестно')

                            # Нормализуем формат
                            content_type= self._normalize_format(content_type)



                            # Если значения None, заменяем на 'Неизвестно'
                            station_name = station_name if station_name else 'Неизвестно'
                            genre = genre if genre else 'Неизвестно'
                            
                            info = f"[OK][STREAM][{station_name}][{content_type}][{bitrate}][{genre}]"
                            active_count += 1
                else:
                    # Считаем все не-200 статусы мертвыми
                    info = f"[{response.status_code}]"
                    dead_count += 1
                    
            except requests.exceptions.Timeout:
                info = "[Timeout]"
                dead_count += 1
            except requests.exceptions.ConnectionError:
                info = "[ConnError]"
                dead_count += 1
            except Exception as e:
                info = f"[Error]"
                dead_count += 1
            finally:
                try:
                    response.close()
                except:
                    pass
            
            # Обновляем UI
            self.station_checked.emit(row, info)
            
            checked_count += 1
            self.progress_updated.emit(checked_count, total_stations)
            
            # Освобождаем семафор
            semaphore.release()
        
        # Запускаем потоки для каждой станции
        for row, url in stations_data:
            if self.cancel_flag:
                break
                
            # Ждем освобождения семафора
            semaphore.acquire()
            
            if self.cancel_flag:
                semaphore.release()
                break
                
            thread = threading.Thread(target=check_station, args=(row, url))
            thread.daemon = True
            self.threads.append(thread)
            thread.start()
        
        # Ждем завершения всех потоков
        for thread in self.threads:
            thread.join()
        
        if not self.cancel_flag:
            self.check_finished.emit(checked_count, active_count, dead_count)
        else:
            self.check_cancelled.emit()
    
    def cancel_check(self):
        """Отменить проверку"""
        self.cancel_flag = True

class NameFixer(QObject):
    """
    Класс для переименования станций согласно шаблону.
    """
    # Сигналы для обновления UI 
    renaming_started = pyqtSignal(int) # Передаем общее количество
    station_renamed = pyqtSignal(int, str) # Передаем номер строки и новое имя
    renaming_finished = pyqtSignal(int) # Передаем количество обработанных

    def __init__(self):
        super().__init__()

    def parse_info_cell(self, info_text: str) -> dict:
        """
        Парсит текст из ячейки "Информация" и возвращает словарь с тегами.
        Поддерживает старый и новый формат (с [STREAM] и [PL: N]).
        """
        # Новый формат: [OK][STREAM][Radio Name][audio/mpeg][128][Pop]
        # Новый формат плейлиста: [OK][PL: 5][Playlist Name][audio/mpeg][128][Pop]
        # Старый формат: [OK][Radio Name][audio/mpeg][128][Pop]
        
        # Пытаемся распарсить новый формат
        match_new = re.match(r"^\[OK\]\[(STREAM|PL: \d+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]$", info_text)
        if match_new:
            stream_type, realname, codec, bitrate, genre = match_new.groups()
        else:
            # Пытаемся старый формат
            match_old = re.match(r"^\[OK\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]$", info_text)
            if match_old:
                realname, codec, bitrate, genre = match_old.groups()
                stream_type = "STREAM"
            else:
                return None # Станция не активна, пропускаем
        
        # Обработка "Неизвестно"
        if realname == "Неизвестно": realname = None # Будет заменено на OLDNAME
        if bitrate == "Неизвестно": bitrate = "N/A"
        if codec == "Неизвестно": codec = "N/A"
        if genre == "Неизвестно": genre = "N/A"
            
        # Попытка извлечь чистый формат из content-type
        if '/' in codec:
            codec = codec.split('/')[-1].upper()
        
        return {
            "REALNAME": realname,
            "CODEC": codec,
            "BITRATE": bitrate,
            "GENRE": genre
        }

    def build_new_name(self, template: str, oldname: str, info_dict: dict) -> str:
        """
        Формирует новое имя на основе шаблона и данных.
        """
        if not info_dict:
            return oldname # Если не удалось распарсить, оставляем как есть
    
        realname = info_dict["REALNAME"] if info_dict["REALNAME"] else oldname
        # Создаем копию словаря и добавляем OLDNAME
        tags = info_dict.copy()
        tags["OLDNAME"] = oldname
        tags["REALNAME"] = realname

        result = template
        
        # Замена всех возможных тегов
        for tag, value in tags.items():
            result = result.replace(f"[{tag}]", value if value else "N/A")
        return result

    def fix_names(self, stations_data: list, template: str, apply_to_all: bool = True):
        """
        Основной метод переименования.
        :param stations_data: Список кортежей (row, oldname_item, info_item)
        :param template: Шаблон строки
        :param apply_to_all: Если True - обрабатываем все, иначе только первую строку
        """
        processed_count = 0
        self.renaming_started.emit(len(stations_data) if apply_to_all else 1)
        
        for row, oldname_item, info_item in stations_data:
            info_text = info_item.text()
            oldname = oldname_item.text()
            
            info_dict = self.parse_info_cell(info_text)
            if info_dict: # Только если станция активна
                new_name = self.build_new_name(template, oldname, info_dict)
                if new_name != oldname: # Избегаем ненужного изменения
                    oldname_item.setText(new_name)
                    self.station_renamed.emit(row, new_name)
                    processed_count += 1
                else:
                    self.station_renamed.emit(row, oldname) # Просто сигнал
                
                if not apply_to_all:
                    break # Обрабатываем только первую
       
        self.renaming_finished.emit(processed_count)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Загружаем конфигурацию
        self.config = ConfigManager.load_config()
        
        # Устанавливаем размер окна из конфига
        width = int(self.config['Settings'].get('window_width', '800'))
        height = int(self.config['Settings'].get('window_height', '600'))
        self.setGeometry(100, 100, width, height)
        #self.setWindowTitle("Радио менеджер")
        self.current_file_name = "Новый плейлист"
        self.update_window_title() 

        

        # Инициализация плеера
        self.stream_player = StreamPlayer(ConfigManager)
        self.stream_player.info_updated.connect(self.update_station_info)
        self.stream_player.playback_toggled.connect(self.on_playback_toggled)

        # Инициализация проверки станций
        self.station_checker = StationChecker()
        self.station_checker.progress_updated.connect(self.update_check_progress)
        self.station_checker.station_checked.connect(self.update_station_info_cell)
        self.station_checker.check_finished.connect(self.on_check_finished)
        self.station_checker.check_cancelled.connect(self.on_check_cancelled)


        # Лог
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumHeight(60)  # Примерно 4 строки
        self.log_widget.setMinimumHeight(60)
        #self.log_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.log_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log_widget.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_widget.setFont(QFont("Consolas", 10))  # Удобный шрифт
        
        # Создаем статус-бар
        self.status_bar = StatusBar()
        self.setStatusBar(self.status_bar)
        
              
        # Создаем кастомную таблицу
        self.table = TableWidgetWithDrag(0, 4)
        self.table.setHorizontalHeaderLabels(["Название", "Адрес", "Volume", "Информация"])
        # Подключаем сигнал об изменении количества строк
        self.table.row_count_changed.connect(self.on_table_row_count_changed)
        # self.table.window = lambda: self

        # Настройка внешнего вида таблицы
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(True)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(2, IntegerValidatorDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
       
        # self.table.addAction("Прослушать")
        # self.table.addAction("Проверить станцию")
        # self.table.addAction("Изменить")
        # self.table.addAction("Удалить")
    
        
        # Верхняя панель с кнопками (над таблицей)
        top_panel_layout = QHBoxLayout()
        
        # Кнопки открыть/сохранить слева
        self.open_csv_btn = QPushButton("Открыть CSV")
        self.open_csv_btn.setProperty("class", "norm")
        self.open_csv_btn.clicked.connect(self.open_csv)
        
        self.save_csv_btn = QPushButton("Сохранить CSV")
        self.save_csv_btn.setProperty("class", "norm")
        self.save_csv_btn.clicked.connect(self.save_csv)
        
        # Кнопки справа (справка, плеер, настройки)
        # Создаем кнопку плеера и громкость
        player_layout = QVBoxLayout()
        self.player_btn = QPushButton("♫ Поток (F7)")
        self.player_btn.setProperty("class", "norm")
        self.player_btn.setShortcut(QKeySequence("F7"))
        self.player_btn.clicked.connect(self.toggle_playback)

           
        # Создаем кнопку справки
        self.help_btn = QPushButton("Справка")
        self.help_btn.setProperty("class", "norm")
        self.help_btn.clicked.connect(self.show_help)
        
        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.setProperty("class", "norm")
        self.settings_btn.clicked.connect(self.open_settings)
        
        top_panel_layout.addWidget(self.open_csv_btn)
        top_panel_layout.addWidget(self.save_csv_btn)
        top_panel_layout.addStretch()
        top_panel_layout.addWidget(self.help_btn)
        #top_panel_layout.addWidget(self.player_btn)
        top_panel_layout.addWidget(self.settings_btn)
        
        # Поле поиска и кнопки навигации (под таблицей)
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(5)
        

        self.prev_search_btn = QPushButton("▲")
        self.prev_search_btn.setProperty("class", "navi")
        # self.prev_search_btn.setStyleSheet(nav_button_style)
        self.prev_search_btn.clicked.connect(self.prev_search_result)
        self.prev_search_btn.setEnabled(0)
        
        self.next_search_btn = QPushButton("▼")
        self.next_search_btn.setProperty("class", "navi")
        # self.next_search_btn.setStyleSheet(nav_button_style)
        self.next_search_btn.clicked.connect(self.next_search_result)
        self.next_search_btn.setEnabled(0)

        # Создаем поле поиска с индикатором результатов внутри
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск...")
        self.search_edit.setProperty("class", "search")
        # self.search_edit.setObjectName("searchField") #searchField
        self.search_edit.setFixedHeight(34)
        
        # Создаем лейбл для отображения статуса поиска (внутри поля поиска)
        self.search_status_label = QLabel(self.search_edit)
        self.search_status_label.setProperty("class", "searchStatus")
        self.search_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)



        # Инициализируем позицию лейбла
        def update_label_position():
            self.search_status_label.move(self.search_edit.width() - 60, 0)
            self.search_status_label.resize(50, self.search_edit.height())

        # Обработчик изменения размера поля поиска
        self.search_edit.resizeEvent = lambda event: update_label_position()

        # Первоначальная установка позиции
        update_label_position()

        # Вместо добавления search_edit в layout, добавляем контейнер
        search_layout.addWidget(self.prev_search_btn)
        search_layout.addWidget(self.next_search_btn)
        search_layout.addWidget(self.search_edit) 
        search_layout.addWidget(self.player_btn)

        # Плеер 

        # Громкость под кнопкой слушать
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.stream_player.get_saved_volume() * 100))
        self.volume_slider.valueChanged.connect(self.change_volume)
        # self.volume_slider.setFixedWidth(130)
        self.volume_slider.setMinimumWidth(130)
        self.volume_slider.setMaximumWidth(290)

        # Создаем слой для информации о станции
        station_info_layout = QHBoxLayout()
        station_info_layout.setContentsMargins(0, 0, 0, 0)  # Отступ сверху
        # station_info_layout.setMaximumHeight(100)
        # Создаем вертикальный layout для текстовых меток
        station_labels_layout = QVBoxLayout()
        station_labels_layout.setSpacing(0)
        #station_labels_layout.setFixedWidth(0)
        
        # Получаем ширину кнопки плеера
        button_width = self.player_btn.sizeHint().width()

        # Создаем виджет-контейнер для меток с фиксированной шириной
        station_info_container = QWidget()
        station_info_container.setLayout(station_labels_layout)
        # station_info_container.setFixedWidth(140)  # Устанавливаем ширину как у кнопки
        station_info_container.setMinimumWidth(140)
        station_info_container.setMaximumWidth(300)
        station_info_container.setContentsMargins(0, 0, 0, 0)
        # Метки для информации о станции
        self.station_label = QLabel("Станция:")
        self.station_name_label = QLabel("Неизвестно")
        self.track_label = QLabel("Композиция:")
        self.track_name_label = QLabel("Неизвестно")
        self.format_label = QLabel("Формат/Битрейт:")
        self.format_value_label = QLabel("Неизвестно")
        
        # Устанавливаем стиль для меток
        label_style = """
            QLabel {
                font-size: 12px;
                color: #666;
            }
            QLabel[title="true"] {
                font-weight: bold;
                color: #444;
            }
        """
        
        # Применяем стиль и устанавливаем свойство для заголовков
        for label in [self.station_label, self.track_label, self.format_label]:
            label.setStyleSheet(label_style)
            label.setProperty("title", "true")
        
        for label in [self.station_name_label, self.track_name_label, self.format_value_label]:
            label.setStyleSheet(label_style)
        
        # Добавляем метки в layout
        station_labels_layout.addWidget(self.volume_slider)
        station_labels_layout.addWidget(self.station_label)
        station_labels_layout.addWidget(self.station_name_label)
        station_labels_layout.addWidget(self.track_label)
        station_labels_layout.addWidget(self.track_name_label)
        station_labels_layout.addWidget(self.format_label)
        station_labels_layout.addWidget(self.format_value_label)
        station_info_layout.addWidget(station_info_container)
        #station_info_layout.addLayout(station_labels_layout)
        

        # Кнопки управления строками
        self.add_btn = QPushButton("Добавить (Ins)")
        self.add_btn.setShortcut(QKeySequence(Qt.Key.Key_Insert))
        self.add_btn.setProperty("class", "norm")
        self.add_btn.clicked.connect(self.add_row)
        #self.add_btn.setEnabled(0)

        self.edit_btn = QPushButton("Изменить (F2)")
        self.edit_btn.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self.edit_btn.setProperty("class", "norm")
        self.edit_btn.clicked.connect(self.edit_row)
        
        self.del_btn = QPushButton("Удалить (Del)")
        self.del_btn.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.del_btn.setProperty("class", "crit")
        self.del_btn.clicked.connect(self.delete_row)
        
        # Кнопки операций
        self.find_duplicates_btn = QPushButton("Найти дубли")
        self.find_duplicates_btn.setProperty("class", "norm")
        self.find_duplicates_btn.clicked.connect(self.find_duplicates)
        
        self.find_inactive_btn = QPushButton("Поиск битых")
        self.find_inactive_btn.setProperty("class", "norm")
        self.find_inactive_btn.clicked.connect(self.find_inactive)
        
        self.remove_duplicates_btn = QPushButton("Удалить дубли")
        self.remove_duplicates_btn.setProperty("class", "crit")
        self.remove_duplicates_btn.clicked.connect(self.remove_duplicates)
        
        self.remove_inactive_btn = QPushButton("Удалить битые")
        self.remove_inactive_btn.setProperty("class", "crit")
        self.remove_inactive_btn.clicked.connect(self.remove_inactive)
        
        self.fix_names_btn = QPushButton("Фикс Названий")
        self.fix_names_btn.setProperty("class", "norm")
        self.fix_names_btn.clicked.connect(self.fix_names)

        self.fix_https_btn = QPushButton("Фикс HTTPS")
        self.fix_https_btn.setProperty("class", "norm")
        self.fix_https_btn.clicked.connect(self.fix_https)
        
        # Группировка кнопок в колонки
        buttons_layout = QHBoxLayout()
        
        # Левая колонка (добавление и поиск дублей)
        left_buttons = QVBoxLayout()
        left_buttons.addWidget(self.add_btn)
        left_buttons.addWidget(self.find_duplicates_btn)
        left_buttons.addWidget(self.remove_duplicates_btn)
        
        # Средняя колонка (изменение и поиск битых)
        middle_buttons = QVBoxLayout()
        middle_buttons.addWidget(self.edit_btn)
        middle_buttons.addWidget(self.find_inactive_btn)
        #middle_buttons.addWidget(self.fix_https_btn)  # Фикс HTTPS теперь здесь
        middle_buttons.addWidget(self.remove_inactive_btn)
        
        # Правая колонка (удаление и удаление битых)
        right_buttons = QVBoxLayout()
        right_buttons.addWidget(self.del_btn)  # Удалить теперь на одном уровне с Изменить
        #right_buttons.addWidget(self.remove_inactive_btn)
        right_buttons.addWidget(self.fix_https_btn)
        right_buttons.addWidget(self.fix_names_btn)
        #right_buttons.addStretch()
        
        
        # Добавляем все колонки в основной лейаут
        buttons_layout.addLayout(left_buttons)
        buttons_layout.addLayout(middle_buttons)
        buttons_layout.addLayout(right_buttons)
        # buttons_layout.addStretch()
        
        bottom_layout = QHBoxLayout()
        bottom_layout.addLayout(buttons_layout)
        bottom_layout.addStretch()
        bottom_layout.addLayout(station_info_layout)
        
        # Основной лейаут
        main_layout = QVBoxLayout()
        main_layout.addLayout(top_panel_layout)  # Верхняя панель с кнопками
        main_layout.addWidget(self.table, 1)     # Таблица
        main_layout.addLayout(search_layout)     # Строка поиска с кнопкой плеер
        main_layout.addLayout(bottom_layout)     #Кнопки редактирования с информацией
        main_layout.addWidget(self.log_widget)   #Лог
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        
        
        # Добавляем переменные для хранения результатов поиска
        self.search_results = []
        self.current_search_index = -1
        
        # Подключаем сигнал изменения текста в поле поиска
        self.search_edit.textChanged.connect(self.perform_search)

        # Менеджер состояния UI
        self.ui_state_manager = UIStateManager(self)
        self.table.itemSelectionChanged.connect(self.update_selection_state)

    def show_help(self):
        """Показать окно справки"""
        help_dialog = HelpDialog(self)
        help_dialog.exec()

    def on_table_row_count_changed(self, row_count):
        """Обработчик изменения количества строк в таблице"""
        self.ui_state_manager.has_data = row_count > 0
        self.ui_state_manager.update_state()
        self.update_selection_state()
        
    def update_window_title(self):
        """Обновление заголовка окна с именем файла"""
        self.setWindowTitle(f"Радио менеджер [{self.current_file_name}]")

    def update_search_nav_buttons(self):
        """Обновить состояние кнопок навигации и статус поиска"""
        has_results = len(self.search_results) > 0
        self.prev_search_btn.setEnabled(has_results)
        self.next_search_btn.setEnabled(has_results)

        if has_results:
            status = f"{self.current_search_index + 1}/{len(self.search_results)}"
            self.search_status_label.setText(status)
        else:
            self.search_status_label.setText("")
        
    def perform_search(self):
        """Выполнить поиск по таблице"""
        # Сначала сбрасываем всю подсветку
        self.table.reset_all_highlighting()

        search_text = self.search_edit.text().lower()
        self.search_results = []
        self.current_search_index = -1
        self.search_status_label.setText("")  # Сбрасываем статус

        if not search_text:
            self.update_search_nav_buttons()
            return

        # Определяем стиль подсветки в зависимости от запроса
        highlight_style = "error" if search_text == "[double]" else "highlight"


        # Ищем совпадения
        for row in range(self.table.rowCount()):
            for col in [0, 1, 3]:  # Поиск по названию, адресу и информации
                item = self.table.item(row, col)
                if item and search_text in item.text().lower():
                    self.search_results.append(row)
                    self.table.highlight_row(row, highlight_style)
                    break

        if self.search_results:
            self.current_search_index = 0
            self.table.selectRow(self.search_results[self.current_search_index])
        
        self.update_search_nav_buttons()
    
    def prev_search_result(self):
        """Перейти к предыдущему результату поиска"""
        if not self.search_results:
            return
            
        self.current_search_index -= 1
        if self.current_search_index < 0:
            self.current_search_index = len(self.search_results) - 1
            
        self.table.selectRow(self.search_results[self.current_search_index])
        self.update_search_nav_buttons()
    
    def next_search_result(self):
        """Перейти к следующему результату поиска"""
        if not self.search_results:
            return
            
        self.current_search_index += 1
        if self.current_search_index >= len(self.search_results):
            self.current_search_index = 0
            
        self.table.selectRow(self.search_results[self.current_search_index])
        self.update_search_nav_buttons()
    
    def open_csv(self):
        """Открытие CSV-файла с валидацией"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл", "", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        self.log("Открытие файла...")
        
        # Очищаем таблицу перед загрузкой
        self.table.setRowCount(0)
        
        # Показываем сообщение в статусбаре
        self.status_bar.show_message("Обработка файла...")
        
        try:
            # Обрабатываем файл синхронно
            data_processor = DataProcessor()
            stations, log_messages = data_processor.process_csv_file(file_path)
            
            # Обновляем таблицу
            self.table.setRowCount(len(stations))
            for row, station in enumerate(stations):
                self.table.setItem(row, 0, QTableWidgetItem(station['name']))
                self.table.setItem(row, 1, QTableWidgetItem(station['url']))
                item = QTableWidgetItem(str(station['volume']))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, 2, item)
                item_info = QTableWidgetItem("-")
                item_info.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, 3, item_info)
            self.ui_state_manager.has_data = self.table.rowCount() > 0
            self.ui_state_manager.update_state()
            # Выводим логи
            for msg in log_messages:
                self.log(msg)
            # Обновляем заголовок окна
            self.current_file_name = Path(file_path).name
            self.update_window_title()

            self.status_bar.show_message("Файл загружен", 3)
            
        
        except Exception as e:
            self.log(f"Ошибка при обработке файла: {str(e)}")
            self.status_bar.show_message("Ошибка загрузки файла", 3)

    def save_csv(self):
        """Сохранение таблицы в формате CSV"""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Сохранение", "Таблица пуста.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить файл", "", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        # Собираем данные из таблицы
        stations = []
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip()
            url = self.table.item(row, 1).text().strip()
            volume = self.table.item(row, 2).text().strip()
            stations.append({
                'name': name,
                'url': url,
                'volume': int(volume) if volume else 0
            })
        
        # Сохраняем файл
        try:
            data_processor = DataProcessor()
            success, message = data_processor.save_csv_file(file_path, stations)
            
            if success:
                self.log(message)
                # Обновляем заголовок окна
                self.current_file_name = Path(file_path).name
                self.update_window_title()

            else:
                self.log(message)
                QMessageBox.critical(self, "Ошибка", message)
                
        except Exception as e:
            error_msg = f"Ошибка при сохранении файла: {str(e)}"
            self.log(error_msg)
            QMessageBox.critical(self, "Ошибка", error_msg)
    # Заглушки для новых методов (реализацию добавите позже)
 
    def find_duplicates(self):
        """Поиск дубликатов станций по URL"""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Поиск дублей", "Таблица пуста.")
            return
        
        # Сбрасываем предыдущие результаты
        self.table.reset_all_highlighting()
        self.duplicates = []
        self.has_duplicates = False
        
        url_dict = {}  # Словарь для хранения URL и их строк
        
        # Собираем все URL из таблицы
        for row in range(self.table.rowCount()):
            url_item = self.table.item(row, 1)
            if url_item:
                url = url_item.text().strip().lower()
                if url:  # Игнорируем пустые URL
                    if url not in url_dict:
                        url_dict[url] = []
                    url_dict[url].append(row)
        
        # Находим дубликаты
        duplicate_count = 0
        for url, rows in url_dict.items():
            if len(rows) > 1:  # Если URL встречается более одного раза
                # Первую строку не трогаем, остальные помечаем как дубли
                for row in rows[1:]:
                    self.duplicates.append(row)
                    info_item = self.table.item(row, 3)
                    if info_item:
                        info_item.setText("[DOUBLE]")
                    self.table.highlight_row(row, "error")
                duplicate_count += len(rows) - 1
        
            self.has_duplicates = duplicate_count > 0
            self.ui_state_manager.has_duplicates = self.has_duplicates  # Обновляем флаг
            self.ui_state_manager.update_state()

        
        # Обновляем UI
        # self.update_remove_buttons_state()
        
        if duplicate_count > 0:
            # Устанавливаем текст поиска для навигации
            self.search_edit.setText("[DOUBLE]")
            self.perform_search()
            self.log(f"Найдено {duplicate_count} дублей из {self.table.rowCount()} станций")
            # self.button_states.has_duplicates = duplicate_count > 0
            # self.button_states.update_states()
        else:
            self.log("Дубликаты не найдены")

    def remove_duplicates(self):
        """Удаление всех найденных дубликатов"""
        if not self.has_duplicates or not self.duplicates:
            QMessageBox.information(self, "Удаление дублей", "Дубликаты не найдены.")
            return
        
        reply = QMessageBox.question(
            self, 
            "Удаление дублей", 
            f"Вы действительно хотите удалить {len(self.duplicates)} дубликатов?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Удаляем строки с конца, чтобы индексы не сбивались
            for row in sorted(self.duplicates, reverse=True):
                self.table.removeRow(row)
            
            removed_count = len(self.duplicates)
            self.duplicates = []
            self.has_duplicates = False
            # self.button_states.has_duplicates = False
            # self.button_states.update_states()
            
            # Очищаем поиск и подсветку
            # self.search_edit.clear()
            self.table.reset_all_highlighting()
            self.has_duplicates = False
            self.ui_state_manager.has_duplicates = False
            self.ui_state_manager.update_state()
            self.log(f"Удалено {removed_count} дубликатов")
            # Очищаем поиск и подсветку
            self.search_edit.clear()  # Добавлено очищение поля поиска
            self.table.reset_all_highlighting()

    
    def fix_https(self):
        """Простая замена https на http в URL"""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Фикс HTTPS", "Таблица пуста.")
            return

        # Простое сообщение с подтверждением
        reply = QMessageBox.question(
            self,
            "Фикс HTTPS",
            "Для поддержки старых прошивок рекомендуется изменить\n"
            "протокол в адресах с HTTPS на HTTP.\n\n"
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        changed_count = 0
        total_count = self.table.rowCount()

        for row in range(total_count):
            url_item = self.table.item(row, 1)
            if url_item:
                url = url_item.text().strip()
                if url.startswith("https://"):
                    new_url = url.replace("https://", "http://", 1)
                    url_item.setText(new_url)
                    changed_count += 1

        # Простой отчет в лог
        if changed_count > 0:
            self.log(f"Заменено HTTPS на HTTP: {changed_count}/{total_count} станций")
        else:
            self.log("URL с HTTPS не найдены")

    def fix_names(self):
        """Фикс Названий"""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Фикс Названий", "Таблица пуста.")
            return

        # Получаем текущий шаблон из настроек
        template = self.config['Settings'].get('rename_template', DEFAULT_RENAME_TEMPLATE)

        # Создаем и открываем диалог
        dialog = RenameTemplateDialog(template, self)
        result = dialog.exec()

        if result == QDialog.DialogCode.Rejected: # Отменено
            return

        # Получаем шаблон из диалога (он мог измениться)
        new_template = dialog.get_template()

        # Собираем данные для переименования
        stations_data_for_renaming = []
        for row in range(self.table.rowCount()):
            oldname_item = self.table.item(row, 0)
            info_item = self.table.item(row, 3)
            if oldname_item and info_item:
                stations_data_for_renaming.append((row, oldname_item, info_item))

        if not stations_data_for_renaming:
            self.log("Нет данных станций для переименования.")
            return

        # Определяем, применять ко всем или к выделенному
        apply_to_all = (result == 1) # 1 это "Все"
        # Если "Выбранный", то найдем выделенную строку
        selected_row = self.table.currentRow()
        if not apply_to_all:
            if selected_row < 0 or selected_row >= len(stations_data_for_renaming):
                 QMessageBox.warning(self, "Фикс Названий", "Выберите строку для применения.")
                 return
            # Фильтруем список до одной строки
            stations_data_for_renaming = [stations_data_for_renaming[selected_row]]

        # Применяем переименование
        self.log(f"Начинаем фикс названий по шаблону: {new_template}")
        name_fixer = NameFixer()
        try:
            name_fixer.fix_names(stations_data_for_renaming, new_template, apply_to_all)
            self.log("Фикс названий завершён.")
        except Exception as e:
            self.log(f"Ошибка при фиксе названий: {e}")
            QMessageBox.critical(self, "Ошибка", f"Ошибка при фиксе названий:\n{e}")
    

    
    def toggle_playback(self):
        """Переключение воспроизведения"""
        # if self.is_checking:
        #     return
            
        row = self.table.currentRow()
        
        if row < 0:
            # Если нет выделенной строки, останавливаем воспроизведение
            if self.stream_player.is_currently_playing():
                self.stream_player.stop()
            return
        
        # Получаем URL из выделенной строки
        url_item = self.table.item(row, 1)
        if not url_item:
            return
            
        url = url_item.text().strip()
        if not url:
            return
        
        # Переключаем воспроизведение
        if self.stream_player.toggle_playback(row, url):

            info_item = self.table.item(row, 3)
            oldname = self.table.item(row, 0).text() if self.table.item(row, 0) else "Неизвестно"
            if info_item:
                parsed = self._parse_info_from_cell(info_item.text())
                if parsed:
                    station_name = parsed.get("REALNAME", oldname) or oldname
                    bitrate = parsed.get("BITRATE", "N/A")
                    codec = parsed.get("CODEC", "N/A")
                    self.station_name_label.setText(station_name)
                    self.format_value_label.setText(f"{codec}/{bitrate}")
                    self.track_name_label.setText("-")     # пока нет трека
                else:
                    self.station_name_label.setText(oldname)
                    self.format_value_label.setText("-")
                    self.track_name_label.setText("-")
            else:
                self.station_name_label.setText(oldname)
                self.format_value_label.setText("-")
                self.track_name_label.setText("-")

        else:
            # Остановили воспроизведение
            self.station_name_label.setText("Неизвестно")
            self.track_name_label.setText("Неизвестно")
            self.format_value_label.setText("Неизвестно")

    def on_playback_toggled(self, is_playing):
        """Обработчик изменения состояния воспроизведения"""
        if is_playing:
            self.player_btn.setText("■ Поток (F7)")
        else:
            self.player_btn.setText("♫ Поток (F7)")
        self.player_btn.setShortcut(QKeySequence("F7"))    

    def update_station_info(self, station_name, track_name, format_info):
        """Обновление информации о станции"""
        self.station_name_label.setText(station_name)
        self.track_name_label.setText(track_name)
        self.format_value_label.setText(format_info)

    def change_volume(self, value):
        """Изменение громкости"""
        volume = value / 100.0
        self.stream_player.set_volume(volume)

    def find_inactive(self):
        """Поиск битых станций"""

        # Останавливаем плеер, если он запущен
        if self.stream_player.is_currently_playing():
            self.threading_log("Останавливаем плеер перед началом проверки")
            self.stream_player.stop()


        self.ui_state_manager.is_checking = True
        self.ui_state_manager.update_state()

        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Проверка", "Таблица пуста.")
            return
        
        # if self.is_checking:
        #     return
        
        # Получаем настройки из конфига
        max_threads = int(self.config['Settings'].get('max_check_threads', '10'))
        timeout = int(self.config['Settings'].get('check_timeout', '10'))
        self.station_checker.max_threads = max_threads
        self.station_checker.set_timeout(timeout)
        

        self.find_inactive_btn.setText("Отмена")
        self.find_inactive_btn.clicked.disconnect()
        self.find_inactive_btn.clicked.connect(self.cancel_check)
        
        # Блокируем таблицу
        
        
        # Инициализируем переменные
        # self.is_checking = True
        self.has_checked_stations = False
        
        # Собираем данные для проверки
        stations_data = []
        for row in range(self.table.rowCount()):
            url_item = self.table.item(row, 1)
            if url_item:
                url = url_item.text().strip()
                if url:
                    stations_data.append((row, url))
        
        if not stations_data:
            self.log("Нет станций для проверки")
            # self.finish_check()
            return
        
        self.log(f"Запущен поиск битых станций. Всего: {len(stations_data)}")
        self.status_bar.show_message(f"Проверено 0 из {len(stations_data)}")
        self.status_bar.show_progress(True)
        self.status_bar.set_progress_range(0, len(stations_data))
        
        # Запускаем проверку в отдельном потоке
        self.check_thread = threading.Thread(target=self.station_checker.check_stations, args=(stations_data,))
        self.check_thread.daemon = True
        self.check_thread.start()

    def _parse_info_from_cell(self, text):
        """
        Парсит строку из ячейки «Информация», возвращающую словарь
        со значениями REALNAME, CODEC, BITRATE и GENRE. Возвращает None,
        если строка не соответствует формату.
        """
        # Пример: [OK][STREAM][Radio Name][audio/mpeg][128][Pop]
        #          [OK][PL: 3][Playlist Name][audio/mpeg][128][Pop]
        match_new = re.match(r"^\[OK\]\[(STREAM|PL: \d+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]$", text)
        if match_new:
            stream_type, realname, codec, bitrate, genre = match_new.groups()
            return {
                "REALNAME": realname or None,
                "CODEC": codec or None,
                "BITRATE": bitrate or None,
                "GENRE": genre or None
            }
        # Старый формат: [OK][Radio Name][audio/mpeg][128][Pop]
        match_old = re.match(r"^\[OK\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]$", text)
        if match_old:
            realname, codec, bitrate, genre = match_old.groups()
            return {
                "REALNAME": realname or None,
                "CODEC": codec or None,
                "BITRATE": bitrate or None,
                "GENRE": genre or None
            }
        return None

    def cancel_check(self):
        """Отмена проверки"""
        # if not self.is_checking:
        #     return
            
        reply = QMessageBox.question(self, "Отмена", "Остановить проверку станций?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.log("Остановка проверки, ожидаем ответа станций")
            self.station_checker.cancel_check()

    def update_check_progress(self, checked, total):
        """Обновление прогресса проверки"""
        self.status_bar.set_progress(checked)
        self.status_bar.show_message(f"Проверено {checked} из {total}")

    def update_station_info_cell(self, row, info):
        """Обновление ячейки информации о станции"""
        info_item = self.table.item(row, 3)
        if info_item:
            info_item.setText(info)
            # Если найдена битая станция
            if info.startswith(("[404]", "[Error]", "[ConnError]", "[Timeout]")):
                self.ui_state_manager.found_inactive = True
                self.ui_state_manager.update_state()

    def on_check_finished(self, checked_count, active_count, dead_count):
        """Завершение проверки"""

        self.ui_state_manager.is_checking = False
        self.ui_state_manager.check_completed = True
        self.ui_state_manager.update_state()

        # self.is_checking = False
        self.has_checked_stations = (checked_count > 0)
        
        self.log(f"Проверка окончена. Проверено: {checked_count}, Активных: {active_count}, Мертвых: {dead_count}")
        
        # Восстанавливаем UI
        # self.finish_check()
        # Восстанавливаем кнопки

        self.find_inactive_btn.setText("Поиск битых")
        self.find_inactive_btn.clicked.disconnect()
        self.find_inactive_btn.clicked.connect(self.find_inactive)
        
        # Скрываем прогрессбар
        self.status_bar.show_progress(False)

    def on_check_cancelled(self):
        """Отмена проверки пользователем"""
        self.ui_state_manager.is_checking = False
        self.ui_state_manager.update_state()

        # self.is_checking = False
        self.has_checked_stations = True  # Раз проверка началась, делаем кнопки активными
        
        self.log("Проверка отменена пользователем")
        # self.finish_check()
        self.ui_state_manager.is_checking = False
        self.ui_state_manager.check_completed = True
        self.ui_state_manager.update_state()

        """Завершение проверки и восстановление UI"""
        # Восстанавливаем кнопки

        self.find_inactive_btn.setText("Поиск битых")
        self.find_inactive_btn.clicked.disconnect()
        self.find_inactive_btn.clicked.connect(self.find_inactive)
        
        # Скрываем прогрессбар
        self.status_bar.show_progress(False)


    def remove_inactive(self):
        """Удаление неактивных станций с выбором флагов"""
        if not self.has_checked_stations:
            QMessageBox.information(
                self, "Удаление", 
                "Сначала выполните проверку станций."
            )
            return
        
        # Загружаем текущие настройки флагов из конфига
        config = ConfigManager.load_config()
        selected_flags = {}
        for flag in ["404", "Error", "ConnError", "Timeout"]:
            value = config['Settings'].get(f'delete_{flag}', 'True')
            selected_flags[flag] = value.lower() in ['true', '1', 'yes']
        
        # Диалог выбора флагов
        dialog = DeleteInactiveDialog(selected_flags, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Получаем выбранные флаги
        selected_flags = dialog.get_selected_flags()
        if not selected_flags:
            QMessageBox.information(
                self, "Удаление", 
                "Не выбрано ни одного флага для удаления."
            )
            return
        
        # Сохраняем выбор пользователя в конфиг
        for flag in ["404", "Error", "ConnError", "Timeout"]:
            config['Settings'][f'delete_{flag}'] = str(f"[{flag}]" in selected_flags).lower()
        ConfigManager.save_config(config)

        # Подсчитываем станции с выбранными флагами
        dead_rows = []
        for row in range(self.table.rowCount()):
            info_item = self.table.item(row, 3)
            if info_item and any(flag in info_item.text() for flag in selected_flags):
                dead_rows.append(row)
        
        if not dead_rows:
            QMessageBox.information(
                self, "Удаление", 
                "Станций с выбранными флагами не найдено."
            )
            return
        
        # Подтверждение удаления
        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить {len(dead_rows)} станций со следующими флагами:\n" +
            ", ".join(selected_flags) + "?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Удаляем строки в обратном порядке
            for row in sorted(dead_rows, reverse=True):
                self.table.removeRow(row)
            
            self.log(f"Удалено {len(dead_rows)} неактивных станций.")
            self.has_checked_stations = False  # Сбрасываем статус проверки
            self.ui_state_manager.check_completed = False
            self.ui_state_manager.has_data = self.table.rowCount() > 0
            self.ui_state_manager.update_state()
            # Сбрасываем флаги после удаления
            self.ui_state_manager.found_inactive = False
            self.ui_state_manager.check_completed = False
            self.ui_state_manager.update_state()

    def closeEvent(self, event):
        # Сохраняем размер окна перед закрытием
        self.config['Settings']['window_width'] = str(self.width())
        self.config['Settings']['window_height'] = str(self.height())
        ConfigManager.save_config(self.config)
        event.accept()        
        
    def open_settings(self):
        current_theme = self.config['Settings'].get('theme', 'light')
        max_threads = int(self.config['Settings'].get('max_check_threads', '10'))
        current_timeout = int(self.config['Settings'].get('check_timeout', '10'))
        current_template = self.config['Settings'].get('rename_template', DEFAULT_RENAME_TEMPLATE)
        
        # Передать current_template в диалог
        dialog = SettingsDialog(current_theme, max_threads, current_timeout, current_template, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_theme = dialog.get_selected_theme()
            new_threads = dialog.get_max_threads()
            new_timeout = dialog.get_timeout()
            new_template = dialog.get_rename_template() # Получаем шаблон
            
            changed = False
            
            if new_theme != current_theme:
                self.config['Settings']['theme'] = new_theme
                changed = True
                ThemeManager.apply_theme(new_theme)
            
            if new_threads != max_threads:
                self.config['Settings']['max_check_threads'] = str(new_threads)
                changed = True
                
            if new_timeout != current_timeout:
                self.config['Settings']['check_timeout'] = str(new_timeout)
                changed = True
                self.station_checker.set_timeout(new_timeout)

            # Сохраняем шаблон, если он изменился
            if new_template != current_template:
                self.config['Settings']['rename_template'] = new_template
                changed = True
            
            if changed:
                ConfigManager.save_config(self.config)
            

    def insert_row(self, row_position, data):
        self.table.insertRow(row_position)
        for col, value in enumerate(data):
            item = QTableWidgetItem(value)
            
            # Настройка флагов:
            if col == 3:  # 4-й столбец (Информация)
                # Разрешаем выделение, но запрещаем редактирование
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:  # Первые три столбца
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            
            # Особое выравнивание для столбца Volume
            if col == 2:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                
            self.table.setItem(row_position, col, item)
            
        self.table.selectRow(row_position)
    def update_selection_state(self):
        """Обновляет состояние выделенной строки"""
        self.ui_state_manager.row_selected = self.table.currentRow() >= 0
        self.ui_state_manager.update_state()

    def add_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            insert_position = current_row + 1  # После выделенной строки
        else:
            insert_position = 0  # В начало таблицы
            
        default_data = ["Новая станция", "Введите адрес", "0", "-"]
        self.insert_row(insert_position, default_data)

        self.ui_state_manager.has_data = self.table.rowCount() > 0
        self.ui_state_manager.update_state()
        self.update_selection_state()

    def edit_row(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Ошибка", "Выберите строку для редактирования")
            return
        
        data = [
            self.table.item(row, 0).text(),
            self.table.item(row, 1).text(),
            self.table.item(row, 2).text()
        ]
        
        dialog = EditDialog(data, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_data = dialog.get_data()
            for col, text in enumerate(new_data):
                self.table.item(row, col).setText(text)

    def delete_row(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Ошибка", "Выберите строку для удаления")
            return
        
        self.table.removeRow(row)
        if self.table.rowCount() > 0:
            self.table.selectRow(min(row, self.table.rowCount()-1))

        # self.ui_state_manager.has_data = self.table.rowCount() > 0
        self.update_selection_state()    

    def log(self, message):
        """Добавляет сообщение в лог с автопрокруткой вниз"""
        self.log_widget.append(message)
        self.log_widget.moveCursor(QTextCursor.MoveOperation.End)
        self.log_widget.ensureCursorVisible()       


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Загружаем конфигурацию
    config = ConfigManager.load_config()
    
    # Применяем тему из конфига
    ThemeManager.apply_theme(config['Settings'].get('theme', 'light'))
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())