import logging
import re

from binsync.common.controller import BinSyncController
from binsync.common.ui.qt_objects import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    Qt,
    QTableWidget,
    QTableWidgetItem,
)
from binsync.common.ui.utils import QNumericItem, friendly_datetime
from binsync.data.state import State
from binsync.core.scheduler import SchedSpeed

l = logging.getLogger(__name__)

class QGlobalItem:
    def __init__(self, name, type_, user, last_push):
        self.name = name
        self.type = type_
        self.user = user
        self.last_push = last_push

    def __eq__(self, other):
        return self.name == other.name and self.type == other.type

    def __hash__(self):
        return sum((self.type + self.name).encode())

    def widgets(self):
        # sort by int value
        name = QTableWidgetItem(self.name)
        type_ = QTableWidgetItem(self.type)
        user = QTableWidgetItem(self.user)

        # sort by unix value
        last_push = QNumericItem(friendly_datetime(self.last_push))
        last_push.setData(Qt.UserRole, self.last_push)

        widgets = [
            name,
            type_,
            user,
            last_push
        ]

        for w in widgets:
            w.setFlags(w.flags() & ~Qt.ItemIsEditable)

        return widgets


class QGlobalsTable(QTableWidget):
    FUNCTION_MAP = {
        "get Struct": "get_struct",
        "get Variable": "get_global_var",
        "get Enum": "get_enum",

        "fill Struct": "fill_struct",
        "fill Variable": "fill_global_var",
        "fill Enum": "fill_enum"
    }

    HEADER = [
        'Name',
        'Type',
        'User',
        'Last Push'
    ]

    def __init__(self, controller: BinSyncController, parent=None):
        super(QGlobalsTable, self).__init__(parent)
        self.controller = controller
        self.items = dict()
        self.last_table = set()

        self.setColumnCount(len(self.HEADER))
        self.setHorizontalHeaderLabels(self.HEADER)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setHorizontalScrollMode(self.ScrollPerPixel)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignHCenter | Qt.Alignment(Qt.TextWordWrap))
        self.horizontalHeader().setMinimumWidth(160)
        self.setHorizontalScrollMode(self.ScrollPerPixel)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(24)

        self.setSortingEnabled(True)

    def reload(self):
        self.setSortingEnabled(False)
        self.setRowCount(len(self.items))
        new_table = set(self.items.values())
        new_entries = new_table.difference(self.last_table)

        for idx, item in enumerate(new_entries):
            for i, attr in enumerate(item.widgets()):
                self.setItem(idx, i, attr)

        self.last_table = new_table
        self.viewport().update()
        self.setSortingEnabled(True)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setObjectName("binsync_global_table_context_menu")

        # create a nested menu
        selected_row = self.rowAt(event.pos().y())
        item0 = self.item(selected_row, 0)
        item1 = self.item(selected_row, 1)
        if any(x is None for x in [item0, item1]):
            return
        global_name = item0.text()
        global_type = item1.text()

        filler_func = self.FUNCTION_MAP["fill " + global_type]
        if not filler_func:
            l.warning(f"Invalid global table sync option: {global_type}")
            return

        menu.addAction("Sync", lambda: filler_func(global_name, user=username))
        from_menu = menu.addMenu("Sync from...")
        for username in self._get_valid_users_for_global(global_name, global_type):
            action = from_menu.addAction(username)
            action.triggered.connect(lambda chck, name=username: filler_func(global_name, user=name))

        menu.popup(self.mapToGlobal(event.pos()))

    def update_table(self):
        for user in self.controller.users():
            state = self.controller.client.get_state(user=user.name)

            all_artifacts = ((state.enums, "Enum"), (state.structs, "Struct"), (state.global_vars, "Variable"))
            for user_artifacts, global_type in all_artifacts:
                new_artifacts = filter(lambda x: (x.last_change or 0) > self.items[x.name].last_change)
                newest_artifact = max(new_artifacts, lambda x: x.last_change)
                self.items[newest_artifact.name] = \
                    QGlobalItem(newest_artifact.name, global_type, user.name, newest_artifact.last_change)

    def _get_valid_users_for_global(self, global_name, global_type):
        global_getter = map(global_type)

        if not global_getter:
            l.warning("Failed to get a valid type for global type")
            return

        for user in self.controller.users(priority=SchedSpeed.FAST):
            user_state: State = self.controller.client.get_state(user=user.name, priority=SchedSpeed.FAST)
            get_global = getattr(user_state, global_getter)
            user_global = get_global(global_name)

            # function must be changed by this user
            if not user_global or not user_global.last_change:
                continue

            yield user.name
