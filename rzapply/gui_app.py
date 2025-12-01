from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from models import DEFAULT_OWNER_TYPE, OWNER_TYPE_ID_OPTIONS, Task, TaskStatus
from task_loader import TaskLoader
from uploader import TaskUploader


class UploadWorker(QObject):
    finished = Signal(Task, bool, str)
    log = Signal(str)

    def __init__(self, task: Task, uploader: TaskUploader):
        super().__init__()
        self.task = task
        self.uploader = uploader

    @Slot()
    def run(self) -> None:
        def emit_log(message: str) -> None:
            text = f"{self.task.display_name()} · {message}"
            self.log.emit(text)
        emit_log("开始执行上传线程")

        try:
            self.uploader.upload(self.task, emit_log)
            self.finished.emit(self.task, True, "上传成功")
        except Exception as exc:
            self.finished.emit(self.task, False, str(exc))


class TaskDetailWidget(QWidget):
    config_saved = Signal(Task)
    upload_requested = Signal(Task)
    OWNER_TYPE_OPTIONS = list(OWNER_TYPE_ID_OPTIONS.keys())
    SUBMIT_ROLES = ["代理人", "申请人"]

    def __init__(self):
        super().__init__()
        self.current_task: Task | None = None
        self.owner_rows: list[dict[str, QWidget]] = []
        self.meta_inputs: dict[str, dict[str, Any]] = {}

        main_layout = QVBoxLayout(self)
        self.status_label = QLabel("请选择任务")
        main_layout.addWidget(self.status_label)

        account_widget = QWidget()
        account_layout = QGridLayout(account_widget)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setHorizontalSpacing(12)
        account_layout.setVerticalSpacing(8)

        account_layout.addWidget(QLabel("登录类型"), 0, 0)
        self.login_type_input = QComboBox()
        self.login_type_input.addItems(["机构", "个人用户"])
        self.login_type_input.setFixedWidth(140)
        self.login_type_input.currentTextChanged.connect(self._handle_login_type_changed)
        account_layout.addWidget(self.login_type_input, 0, 1)

        account_layout.addWidget(QLabel("办理身份"), 0, 2)
        self.submit_role_input = QComboBox()
        self.submit_role_input.addItems(self.SUBMIT_ROLES)
        self.submit_role_input.setFixedWidth(140)
        self.submit_role_input.currentTextChanged.connect(self._handle_submit_role_changed)
        account_layout.addWidget(self.submit_role_input, 0, 3)

        account_layout.addWidget(QLabel("版权中心登录账号"), 0, 4)
        self.login_username_input = QLineEdit()
        self.login_username_input.setPlaceholderText("请输入账号")
        self.login_username_input.setMinimumWidth(200)
        account_layout.addWidget(self.login_username_input, 0, 5)

        account_layout.addWidget(QLabel("版权中心登录密码"), 0, 6)
        self.login_password_input = QLineEdit()
        self.login_password_input.setPlaceholderText("请输入密码")
        self.login_password_input.setEchoMode(QLineEdit.Password)
        self.login_password_input.setMinimumWidth(200)
        account_layout.addWidget(self.login_password_input, 0, 7)

        account_layout.setColumnStretch(7, 1)
        main_layout.addWidget(account_widget)

        owners_header = QHBoxLayout()
        owners_header.addWidget(QLabel("著作权人配置"))
        owners_header.addStretch()
        self.add_owner_button = QPushButton("新增著作权人")
        self.add_owner_button.clicked.connect(lambda: self._add_owner_row())
        owners_header.addWidget(self.add_owner_button)

        self.owner_scroll = QScrollArea()
        self.owner_scroll.setWidgetResizable(True)
        self.owner_container = QWidget()
        self.owner_layout = QVBoxLayout(self.owner_container)
        self.owner_layout.setAlignment(Qt.AlignTop)
        self.owner_scroll.setWidget(self.owner_container)

        self.owner_section = QWidget()
        owner_section_layout = QVBoxLayout(self.owner_section)
        owner_section_layout.setContentsMargins(0, 0, 0, 0)
        owner_section_layout.setSpacing(6)
        owner_section_layout.addLayout(owners_header)
        owner_section_layout.addWidget(self.owner_scroll, stretch=1)
        main_layout.addWidget(self.owner_section, stretch=2)

        button_row = QHBoxLayout()
        self.save_button = QPushButton("保存配置")
        self.save_button.clicked.connect(self._handle_save)
        button_row.addWidget(self.save_button)

        self.upload_button = QPushButton("上传任务")
        self.upload_button.clicked.connect(self._handle_upload)
        button_row.addWidget(self.upload_button)
        main_layout.addLayout(button_row)

        main_layout.addWidget(QLabel("任务元数据"))
        self.meta_scroll = QScrollArea()
        self.meta_scroll.setWidgetResizable(True)
        self.meta_container = QWidget()
        self.meta_form = QFormLayout(self.meta_container)
        self.meta_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.meta_scroll.setWidget(self.meta_container)
        main_layout.addWidget(self.meta_scroll, stretch=2)

        self._add_owner_row()
        self._set_enabled(False)

    def set_task(self, task: Task | None) -> None:
        self.current_task = task
        if not task:
            self.status_label.setText("请选择任务")
            self._clear_owner_rows()
            self._add_owner_row()
            self._clear_meta_fields()
            self._set_login_fields("机构", "", "")
            self._set_submit_role("代理人")
            self._set_enabled(False)
            return

        self._clear_owner_rows()
        owners = task.config.get("owners", [])
        if owners:
            for owner in owners:
                self._add_owner_row(owner)
        else:
            self._add_owner_row()
        self._populate_meta_fields(task)
        login_type = str(task.config.get("login_type", "机构"))
        username = str(task.config.get("login_username", ""))
        password = str(task.config.get("login_password", ""))
        self._set_login_fields(login_type, username, password)
        self._set_submit_role(str(task.config.get("submit_role", "代理人")))
        self.status_label.setText(f"状态：{task.status.name}")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)
        self.upload_button.setEnabled(enabled)
        self.login_type_input.setEnabled(enabled)
        self.login_username_input.setEnabled(enabled)
        self.login_password_input.setEnabled(enabled)
        self.submit_role_input.setEnabled(enabled)
        self._apply_owner_section_state(enabled)

    def _update_remove_buttons_state(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = self.add_owner_button.isEnabled()
        allow_remove = enabled and len(self.owner_rows) > 1
        for row in self.owner_rows:
            row["remove_button"].setEnabled(allow_remove)

    def _sync_task_from_form(self) -> bool:
        if not self.current_task:
            return True
        owners: list[dict[str, str]] = []
        if not self._should_hide_owner_section():
            for row in self.owner_rows:
                owners.append(
                    {
                        "name": row["name"].text().strip(),
                        "card_input": row["card_input"].text().strip(),
                        "province": row["province"].text().strip(),
                        "city": row["city"].text().strip(),
                        "name_type": row["name_type"].currentText().strip(),
                        "id_type": row["id_type"].currentText().strip(),
                    }
                )
        login_type = self.login_type_input.currentText().strip()
        submit_role = self.submit_role_input.currentText().strip()
        login_username = self.login_username_input.text().strip()
        login_password = self.login_password_input.text().strip()
        self.current_task.update_config(
            {
                "owners": owners,
                "login_type": login_type,
                "submit_role": submit_role,
                "login_username": login_username,
                "login_password": login_password,
            }
        )

        meta_updates: dict[str, Any] = {}
        for key, payload in self.meta_inputs.items():
            widget = payload.get("widget")
            if isinstance(widget, QLineEdit):
                meta_updates[key] = widget.text().strip()
                continue
            if isinstance(widget, QPlainTextEdit):
                text_value = widget.toPlainText().strip()
                if payload.get("is_json"):
                    if not text_value:
                        origin = payload.get("original_type")
                        meta_updates[key] = [] if origin is list else {}
                        continue
                    try:
                        meta_updates[key] = json.loads(text_value)
                    except json.JSONDecodeError as exc:
                        QMessageBox.warning(self, "JSON 格式错误", f"{key} 字段解析失败：{exc}")
                        return False
                else:
                    meta_updates[key] = text_value
        if meta_updates:
            self.current_task.meta.update(meta_updates)

        self.status_label.setText(f"状态：{self.current_task.status.name}")
        return True

    def _handle_save(self) -> None:
        if not self.current_task:
            return
        if not self._sync_task_from_form():
            return
        self.config_saved.emit(self.current_task)

    def _handle_upload(self) -> None:
        print("===== _handle_upload=====")
        if not self.current_task:
            return
        if not self._sync_task_from_form():
            return
        print("===== emit task=====")
        self.upload_requested.emit(self.current_task)

    def _populate_meta_fields(self, task: Task) -> None:
        self._clear_meta_fields()
        self.meta_inputs = {}
        for key, value in task.meta.items():
            widget: QWidget
            is_json_value = isinstance(value, (dict, list))
            if is_json_value:
                editor = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2))
                editor.setMinimumWidth(600)
                editor.setMinimumHeight(80)
                editor.moveCursor(QTextCursor.Start)
                editor.verticalScrollBar().setValue(editor.verticalScrollBar().minimum())
                widget = editor
            else:
                editor = QLineEdit(str(value))
                editor.setMinimumWidth(600)
                editor.setCursorPosition(0)
                widget = editor
            self.meta_form.addRow(key, widget)
            self.meta_inputs[key] = {
                "widget": widget,
                "is_json": is_json_value,
                "original_type": type(value),
            }

    def _clear_meta_fields(self) -> None:
        while self.meta_form.count():
            item = self.meta_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.meta_inputs.clear()

    def _set_login_fields(self, login_type: str, username: str, password: str) -> None:
        idx = self.login_type_input.findText(login_type) if login_type else -1
        self.login_type_input.blockSignals(True)
        if idx >= 0:
            self.login_type_input.setCurrentIndex(idx)
        else:
            self.login_type_input.setCurrentIndex(0)
        self.login_type_input.blockSignals(False)
        self.login_username_input.setText(username)
        self.login_password_input.setText(password)
        self._apply_owner_section_state()

    def _set_submit_role(self, role: str) -> None:
        idx = self.submit_role_input.findText(role) if role else -1
        self.submit_role_input.blockSignals(True)
        if idx >= 0:
            self.submit_role_input.setCurrentIndex(idx)
        else:
            self.submit_role_input.setCurrentIndex(0)
        self.submit_role_input.blockSignals(False)
        self._apply_owner_section_state()

    def _should_hide_owner_section(self) -> bool:
        return self.submit_role_input.currentText().strip() == "申请人"

    def _apply_owner_section_state(self, base_enabled: bool | None = None) -> None:
        if base_enabled is None:
            base_enabled = self.save_button.isEnabled()
        hide = self._should_hide_owner_section()
        owner_enabled = base_enabled and not hide
        self.owner_section.setVisible(not hide)
        self.add_owner_button.setEnabled(owner_enabled)
        self.owner_container.setEnabled(owner_enabled)
        self._update_remove_buttons_state(owner_enabled)

    def _handle_login_type_changed(self, value: str) -> None:
        self._apply_owner_section_state(self.save_button.isEnabled())
        if self.current_task:
            self._sync_task_from_form()

    def _handle_submit_role_changed(self, value: str) -> None:
        self._apply_owner_section_state(self.save_button.isEnabled())
        if self.current_task:
            self._sync_task_from_form()

    def get_login_credentials(self) -> dict[str, str]:
        """Return current login form values (trimmed)."""
        return {
            "login_type": self.login_type_input.currentText().strip(),
            "submit_role": self.submit_role_input.currentText().strip(),
            "login_username": self.login_username_input.text().strip(),
            "login_password": self.login_password_input.text().strip(),
        }

    def sync_current_task(self) -> bool:
        """Sync current form into task without triggering upload/save explicitly."""
        return self._sync_task_from_form()


    def _add_owner_row(self, data: dict[str, str] | None = None) -> None:
        data = data or {}
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        name_input = QLineEdit()
        name_input.setPlaceholderText("著作权人姓名或名称")
        name_input.setText(data.get("name", ""))

        name_type_input = QComboBox()
        name_type_input.addItems(self.OWNER_TYPE_OPTIONS)
        preset_type = data.get("name_type") or DEFAULT_OWNER_TYPE
        index = name_type_input.findText(preset_type, Qt.MatchExactly)
        if index >= 0:
            name_type_input.setCurrentIndex(index)

        id_type_input = QComboBox()
        self._refresh_id_type_options(name_type_input.currentText(), id_type_input, data.get("id_type"))
        name_type_input.currentTextChanged.connect(
            lambda value, combo=id_type_input: self._refresh_id_type_options(value, combo)
        )

        card_input = QLineEdit()
        card_input.setPlaceholderText("证件号码")
        card_input.setText(data.get("card_input", ""))

        province_input = QLineEdit()
        province_input.setPlaceholderText("所属省份")
        province_input.setText(data.get("province", ""))

        city_input = QLineEdit()
        city_input.setPlaceholderText("所属城市")
        city_input.setText(data.get("city", ""))

        remove_button = QPushButton("删除")
        remove_button.clicked.connect(lambda: self._remove_owner_row(row_widget))

        row_layout.addWidget(QLabel("著作权类型"))
        row_layout.addWidget(name_type_input, stretch=1)
        row_layout.addWidget(QLabel("证件类型"))
        row_layout.addWidget(id_type_input, stretch=1)
        row_layout.addWidget(QLabel("姓名/名称"))
        row_layout.addWidget(name_input, stretch=2)
        row_layout.addWidget(QLabel("证件号码"))
        row_layout.addWidget(card_input, stretch=1)
        row_layout.addWidget(QLabel("省份"))
        row_layout.addWidget(province_input, stretch=1)
        row_layout.addWidget(QLabel("城市"))
        row_layout.addWidget(city_input, stretch=1)
        row_layout.addWidget(remove_button)

        self.owner_layout.addWidget(row_widget)
        self.owner_rows.append(
            {
                "widget": row_widget,
                "name": name_input,
                "name_type": name_type_input,
                "id_type": id_type_input,
                "card_input": card_input,
                "province": province_input,
                "city": city_input,
                "remove_button": remove_button,
            }
        )
        self._update_remove_buttons_state()

    def _refresh_id_type_options(self, owner_type: str, combo: QComboBox, preset: str | None = None) -> None:
        options = OWNER_TYPE_ID_OPTIONS.get(owner_type, OWNER_TYPE_ID_OPTIONS[DEFAULT_OWNER_TYPE])
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(options)
        if preset and preset in options:
            combo.setCurrentText(preset)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _remove_owner_row(self, widget: QWidget) -> None:
        for row in list(self.owner_rows):
            if row["widget"] is widget:
                self.owner_rows.remove(row)
                widget.setParent(None)
                widget.deleteLater()
                break
        self._update_remove_buttons_state()

    def _clear_owner_rows(self) -> None:
        while self.owner_rows:
            row = self.owner_rows.pop()
            row["widget"].setParent(None)
            row["widget"].deleteLater()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("rzapply 上传助手")

        self.files_dir: Path | None = None
        self.loader: TaskLoader | None = None
        self.tasks: list[Task] = []
        self.worker_threads: list[QThread] = []
        self.active_workers: list[UploadWorker] = []
        self.upload_queue: list[Task] = []
        self.current_upload_task: Task | None = None

        central = QWidget()
        layout = QHBoxLayout(central)
        self.setCentralWidget(central)

        left_panel = QVBoxLayout()
        layout.addLayout(left_panel, stretch=1)

        header_row = QHBoxLayout()
        self.choose_button = QPushButton("选择 ZIP 目录")
        self.choose_button.clicked.connect(self._choose_directory)
        header_row.addWidget(self.choose_button)

        self.refresh_button = QPushButton("重新扫描 ZIP")
        self.refresh_button.clicked.connect(self._refresh_tasks)
        self.refresh_button.setEnabled(False)
        header_row.addWidget(self.refresh_button)

        self.batch_upload_button = QPushButton("批量上传")
        self.batch_upload_button.clicked.connect(self._handle_batch_upload)
        self.batch_upload_button.setEnabled(False)
        header_row.addWidget(self.batch_upload_button)

        self.apply_all_button = QPushButton("账号应用到全部")
        self.apply_all_button.clicked.connect(self._handle_apply_credentials_all)
        self.apply_all_button.setEnabled(False)
        header_row.addWidget(self.apply_all_button)

        self.apply_owners_button = QPushButton("著作权人应用到全部")
        self.apply_owners_button.clicked.connect(self._handle_apply_owners_all)
        self.apply_owners_button.setEnabled(False)
        header_row.addWidget(self.apply_owners_button)

        self.delete_button = QPushButton("删除任务")
        self.delete_button.clicked.connect(self._handle_delete_task)
        self.delete_button.setEnabled(False)
        header_row.addWidget(self.delete_button)
        left_panel.addLayout(header_row)

        self.dir_label = QLabel("未选择目录")
        left_panel.addWidget(self.dir_label)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._handle_selection_changed)
        left_panel.addWidget(self.list_widget, stretch=1)

        right_panel = QVBoxLayout()
        layout.addLayout(right_panel, stretch=2)

        self.detail_widget = TaskDetailWidget()
        self.detail_widget.config_saved.connect(self._handle_config_saved)
        self.detail_widget.upload_requested.connect(self._start_upload)
        right_panel.addWidget(self.detail_widget, stretch=3)

        log_header = QHBoxLayout()
        log_label = QLabel("上传日志")
        log_header.addWidget(log_label)
        log_header.addStretch()
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self._clear_logs)
        log_header.addWidget(self.clear_log_button)
        right_panel.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        right_panel.addWidget(self.log_view, stretch=1)

        self.detail_widget.set_task(None)

    def _choose_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 ZIP 目录")
        if not path:
            return
        self.files_dir = Path(path)
        self.loader = TaskLoader(self.files_dir)
        self.dir_label.setText(str(self.files_dir))
        self.refresh_button.setEnabled(True)
        self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        if not self.loader:
            QMessageBox.information(self, "提示", "请先选择 ZIP 目录。")
            return
        self.tasks = self.loader.load_tasks()
        self._render_task_list()
        has_tasks = bool(self.tasks)
        self.batch_upload_button.setEnabled(has_tasks)
        self.apply_all_button.setEnabled(has_tasks)
        self.apply_owners_button.setEnabled(has_tasks)
        self.delete_button.setEnabled(has_tasks and self.list_widget.currentItem() is not None)
        if self.tasks:
            self.list_widget.setCurrentRow(0)
        else:
            self.detail_widget.set_task(None)

    def _render_task_list(self) -> None:
        self.list_widget.clear()
        for task in self.tasks:
            item = QListWidgetItem(self._format_task_label(task))
            item.setData(Qt.UserRole, task)
            self.list_widget.addItem(item)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_logs(self) -> None:
        self.log_view.clear()

    def closeEvent(self, event) -> None:
        running = [thread for thread in self.worker_threads if thread.isRunning()]
        if running:
            reply = QMessageBox.question(
                self,
                "退出确认",
                "仍有任务在上传，确定要退出并等待其完成吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        for thread in list(self.worker_threads):
            if thread.isRunning():
                thread.quit()
                thread.wait()
        super().closeEvent(event)

    def _format_task_label(self, task: Task) -> str:
        return f"{task.display_name()} · {task.status.name}"

    def _handle_selection_changed(self, row: int) -> None:
        item = self.list_widget.item(row)
        task = item.data(Qt.UserRole) if item else None
        self.detail_widget.set_task(task)
        self._update_delete_button(task)

    def _has_login_credentials(self, task: Task) -> bool:
        username = str(task.config.get("login_username", "")).strip()
        password = str(task.config.get("login_password", "")).strip()
        return bool(username and password)

    def _validate_ready_for_upload(self, task: Task) -> bool:
        if not task.is_config_complete():
            QMessageBox.warning(self, "配置不完整", "请填写完整的著作权人信息后再上传。")
            return False
        if not self._has_login_credentials(task):
            QMessageBox.warning(self, "账号信息缺失", "请填写版权中心登录账号和密码后再上传。")
            return False
        return True

    def _handle_config_saved(self, task: Task) -> None:
        self._persist_state()
        updated_row = None
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item and item.data(Qt.UserRole) is task:
                item.setText(self._format_task_label(task))
                updated_row = row
                break
        if updated_row is not None:
            self.list_widget.blockSignals(True)
            self.list_widget.setCurrentRow(updated_row)
            self.list_widget.blockSignals(False)
        else:
            self._render_task_list()
            self._reselect_task(task)

    def _start_upload(self, task: Task) -> None:
        if task.status == TaskStatus.UPLOADING or task is self.current_upload_task:
            QMessageBox.information(self, "上传中", "任务正在上传，请稍候。")
            return
        if not self._validate_ready_for_upload(task):
            return
        self._queue_task_upload(task)
        self._update_delete_button(task)

    def _queue_task_upload(self, task: Task) -> None:
        if task in self.upload_queue:
            self._append_log(f"{task.display_name()} · 已在上传队列中")
            return
        self.upload_queue.append(task)
        self._append_log(f"{task.display_name()} · 加入上传队列（当前排队 {len(self.upload_queue)}）")
        self._start_next_upload()

    def _handle_batch_upload(self) -> None:
        if not self.detail_widget.sync_current_task():
            return
        self._propagate_login_credentials()
        self._persist_state()

        if not self.tasks:
            QMessageBox.information(self, "提示", "暂无任务可上传，请先选择 ZIP 目录并扫描。")
            return

        uploadable: list[Task] = []
        missing_config: list[Task] = []
        missing_auth: list[Task] = []
        in_progress: list[Task] = []
        completed: list[Task] = []

        for task in self.tasks:
            if task.status == TaskStatus.UPLOADING:
                in_progress.append(task)
                continue
            if task.status == TaskStatus.COMPLETED:
                completed.append(task)
                continue
            if not task.is_config_complete():
                missing_config.append(task)
                continue
            if not self._has_login_credentials(task):
                missing_auth.append(task)
                continue
            uploadable.append(task)

        if missing_config:
            names = "、".join(t.display_name() for t in missing_config)
            self._append_log(f"跳过配置不完整的任务：{names}")
            QMessageBox.warning(self, "配置不完整", f"以下任务配置不完整，已跳过：\n{names}")

        if missing_auth:
            names = "、".join(t.display_name() for t in missing_auth)
            self._append_log(f"跳过账号信息缺失的任务：{names}")
            QMessageBox.warning(self, "账号信息缺失", f"以下任务缺少账号或密码，已跳过：\n{names}")

        if in_progress:
            names = "、".join(t.display_name() for t in in_progress)
            self._append_log(f"跳过正在上传的任务：{names}")

        if completed:
            names = "、".join(t.display_name() for t in completed)
            self._append_log(f"跳过已完成的任务：{names}")

        if not uploadable:
            QMessageBox.information(self, "无可上传任务", "没有可上传的任务，请检查配置或状态。")
            return

        self._append_log(
            f"批量上传：可上传 {len(uploadable)} 个，配置缺失 {len(missing_config)}，账号缺失 {len(missing_auth)}，"
            f"进行中 {len(in_progress)}，已完成 {len(completed)}"
        )
        for task in uploadable:
            self._queue_task_upload(task)

    def _start_next_upload(self) -> None:
        if self.current_upload_task:
            self._append_log("队列调度：已有任务正在上传，等待其完成")
            return
        if not self.upload_queue:
            self._append_log("队列调度：没有等待上传的任务")
            return
        self._append_log(f"队列调度：准备开始下一个任务，排队剩余 {len(self.upload_queue)}")
        task = self.upload_queue.pop(0)
        self.current_upload_task = task
        task.status = TaskStatus.UPLOADING
        self._render_task_list()
        self._reselect_task(task)
        self._persist_state()
        self._append_log(f"{task.display_name()} · 开始上传")
        self._update_delete_button(task)

        thread = QThread()
        # 每个任务独立创建 TaskUploader，避免跨线程复用 Playwright 对象
        worker = UploadWorker(task, TaskUploader())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_upload_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.log.connect(self._append_log)
        worker.finished.connect(lambda *args, w=worker: self._cleanup_worker(w))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda t=thread: self._cleanup_thread(t))
        thread.start()
        self.worker_threads.append(thread)
        self.active_workers.append(worker)

    def _propagate_login_credentials(self) -> None:
        """Fill missing login credentials for all tasks using current form values."""
        creds = self.detail_widget.get_login_credentials()
        username = creds.get("login_username", "")
        password = creds.get("login_password", "")
        login_type = creds.get("login_type", "")
        submit_role = creds.get("submit_role", "")

        for task in self.tasks:
            if not task.config.get("login_username"):
                task.config["login_username"] = username
            if not task.config.get("login_password"):
                task.config["login_password"] = password
            # 登录类型/办理身份：只填空，不覆盖已有值，避免不同账号/身份的任务被统一重写
            if login_type and not task.config.get("login_type"):
                task.config["login_type"] = login_type
            if submit_role and not task.config.get("submit_role"):
                task.config["submit_role"] = submit_role

    def _handle_apply_credentials_all(self) -> None:
        if not self.detail_widget.sync_current_task():
            return
        creds = self.detail_widget.get_login_credentials()
        username = creds.get("login_username", "").strip()
        password = creds.get("login_password", "").strip()
        login_type = creds.get("login_type", "").strip()
        submit_role = creds.get("submit_role", "").strip()
        if not username or not password:
            QMessageBox.warning(self, "账号信息缺失", "请先填写账号和密码，再应用到全部任务。")
            return

        for task in self.tasks:
            task.update_config(
                {
                    "login_username": username,
                    "login_password": password,
                    "login_type": login_type or task.config.get("login_type", ""),
                    "submit_role": submit_role or task.config.get("submit_role", ""),
                }
            )

        self._persist_state()
        self._render_task_list()
        current = self.list_widget.currentItem()
        if current:
            self.detail_widget.set_task(current.data(Qt.UserRole))
        self._append_log("账号信息已应用到全部任务")

    def _handle_apply_owners_all(self) -> None:
        if not self.detail_widget.sync_current_task():
            return
        source_task = self.detail_widget.current_task
        if not source_task:
            QMessageBox.information(self, "提示", "请先选择任务。")
            return
        owners = source_task.config.get("owners") or []
        if not owners:
            QMessageBox.warning(self, "著作权人信息缺失", "当前任务未填写著作权人信息，无法应用。")
            return

        applied = 0
        skipped = 0
        for task in self.tasks:
            # 只给空的任务填充，避免覆盖特殊任务
            if task.config.get("owners"):
                skipped += 1
                continue
            task.update_config({"owners": owners})
            applied += 1

        self._persist_state()
        self._render_task_list()
        current = self.list_widget.currentItem()
        if current:
            self.detail_widget.set_task(current.data(Qt.UserRole))
        self._append_log(f"著作权人信息已应用到全部任务：填充 {applied} 个，保留已有 {skipped} 个")

    def _handle_delete_task(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择要删除的任务。")
            return
        task = item.data(Qt.UserRole)
        if not isinstance(task, Task):
            return
        if task is self.current_upload_task or task.status == TaskStatus.UPLOADING:
            QMessageBox.warning(self, "无法删除", "任务正在上传，完成后再删除。")
            return

        reply = QMessageBox.question(
            self,
            "删除确认",
            f"确定要删除任务 “{task.display_name()}” 吗？不会删除 ZIP/解压文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 如果在队列中，移除
        if task in self.upload_queue:
            self.upload_queue = [t for t in self.upload_queue if t is not task]
            self._append_log(f"{task.display_name()} · 已从上传队列移除")

        # 从任务列表移除并更新 UI
        try:
            self.tasks.remove(task)
        except ValueError:
            pass

        self._render_task_list()
        if self.tasks:
            self.list_widget.setCurrentRow(0)
            self.detail_widget.set_task(self.list_widget.item(0).data(Qt.UserRole))
        else:
            self.detail_widget.set_task(None)
        self._persist_state()
        self._append_log(f"{task.display_name()} · 已删除（文件未删除）")
        self._update_delete_button(self.list_widget.currentItem().data(Qt.UserRole) if self.list_widget.currentItem() else None)

    def _handle_upload_finished(self, task: Task, success: bool, message: str) -> None:
        task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        self._persist_state()
        self._render_task_list()
        self._reselect_task(task)
        self.detail_widget.set_task(task)

        if success:
            self._append_log(f"{task.display_name()} · 上传成功")
        else:
            self._append_log(f"{task.display_name()} · 上传失败：{message}")
            QMessageBox.critical(self, "上传失败", message)

        self.current_upload_task = None
        self._append_log(f"队列剩余 {len(self.upload_queue)} 个等待上传")
        # 异步调度下一个，避免在信号回调里直接启动新线程导致交叉
        QTimer.singleShot(0, self._start_next_upload)
        self._update_delete_button(self.list_widget.currentItem().data(Qt.UserRole) if self.list_widget.currentItem() else None)

    def _reselect_task(self, task: Task) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item and item.data(Qt.UserRole) is task:
                self.list_widget.setCurrentRow(row)
                item.setText(self._format_task_label(task))
                break
        self._update_delete_button(task)

    def _update_delete_button(self, task: Task | None) -> None:
        if not task:
            self.delete_button.setEnabled(False)
            return
        if task is self.current_upload_task or task.status == TaskStatus.UPLOADING:
            self.delete_button.setEnabled(False)
        else:
            self.delete_button.setEnabled(True)
        self.apply_all_button.setEnabled(bool(self.tasks))
        self.apply_owners_button.setEnabled(bool(self.tasks))

    def _persist_state(self) -> None:
        if not self.files_dir:
            return
        payload = []
        for task in self.tasks:
            payload.append(
                {
                    "zip_path": str(task.zip_path),
                    "config": task.config,
                    "status": task.status.name,
                    "meta": task.meta,
                }
            )
        output_path = self.files_dir / "data.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cleanup_thread(self, thread: QThread) -> None:
        try:
            self.worker_threads.remove(thread)
        except ValueError:
            pass

    def _cleanup_worker(self, worker: UploadWorker) -> None:
        try:
            self.active_workers.remove(worker)
        except ValueError:
            pass


def run_gui() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1200, 700)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
