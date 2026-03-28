"""
Main sync dialog for Arena KiCad Library Sync.

Layout matches the original KiCad Arena Sync plugin UX:
- Top: Inline Arena connection panel with saved creds + workspace dropdown
- Middle: Sync controls (Pull / Push / Bidirectional)
- Bottom: Progress bar, log, action buttons
"""

import logging
import threading

logger = logging.getLogger(__name__)

try:
    import wx
except ImportError:
    wx = None


def _require_wx():
    if wx is None:
        raise ImportError("wxPython is required for UI dialogs")


# ---------------------------------------------------------------------------
# Arena Connection Panel (inline at top of dialog)
# ---------------------------------------------------------------------------

class ArenaLoginPanel(wx.Panel):
    """Inline login panel with saved credentials and workspace dropdown.

    If credentials are saved: shows "Saved account: email" + workspace
    dropdown + "Connect to Arena" / "Change account" buttons.

    If no saved credentials: shows email/password fields + workspace
    dropdown + "Connect to Arena" button + save checkbox.
    """

    def __init__(self, parent, config, on_connected):
        super().__init__(parent)
        self.config = config
        self._on_connected = on_connected
        self.client = None
        self.arena_api = None

        sizer = wx.StaticBoxSizer(wx.VERTICAL, self, "Arena Connection")
        inner = sizer.GetStaticBox()

        # Check for saved credentials
        email = config.arena_email if config else ""
        password = config.get_arena_password() if config else None

        if email and password:
            self._build_quick_connect(sizer, inner, email, password)
        else:
            self._build_full_login(sizer, inner)

        self.SetSizer(sizer)

    def _build_quick_connect(self, sizer, parent, email, password):
        """One-click connect for saved credentials with workspace toggle."""
        self._saved_email = email
        self._saved_password = password

        label = wx.StaticText(parent, label=f"Saved account: {email}")
        sizer.Add(label, 0, wx.ALL, 5)

        # Workspace toggle
        ws_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ws_sizer.Add(wx.StaticText(parent, label="Workspace:"), 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.quick_workspace = wx.Choice(parent, choices=["Production", "Sandbox"])
        # Select saved workspace
        saved_ws = self.config.arena_workspace_id if self.config else ""
        from ..arena_client import ArenaClient
        sel = 1 if saved_ws == str(ArenaClient.WORKSPACES.get("Sandbox", "")) else 0
        self.quick_workspace.SetSelection(sel)
        ws_sizer.Add(self.quick_workspace, 0, wx.RIGHT, 10)
        sizer.Add(ws_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.connect_btn = wx.Button(parent, label="Connect to Arena")
        self.connect_btn.Bind(wx.EVT_BUTTON, self._on_quick_connect)
        btn_sizer.Add(self.connect_btn, 0, wx.RIGHT, 5)

        change_btn = wx.Button(parent, label="Change account")
        change_btn.Bind(wx.EVT_BUTTON, self._on_change_account)
        btn_sizer.Add(change_btn, 0)

        sizer.Add(btn_sizer, 0, wx.ALL, 5)

        self.status_label = wx.StaticText(parent, label="")
        sizer.Add(self.status_label, 0, wx.ALL, 5)

    def _build_full_login(self, sizer, parent):
        """Email/password fields with workspace selector."""
        self._saved_email = None
        self._saved_password = None

        grid = wx.FlexGridSizer(4, 2, 5, 5)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(parent, label="Email:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.email_input = wx.TextCtrl(parent, size=(250, -1))
        grid.Add(self.email_input, 1, wx.EXPAND)

        grid.Add(wx.StaticText(parent, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.password_input = wx.TextCtrl(parent, style=wx.TE_PASSWORD, size=(250, -1))
        grid.Add(self.password_input, 1, wx.EXPAND)

        grid.Add(wx.StaticText(parent, label="Workspace:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.workspace_choice = wx.Choice(parent, choices=["Production", "Sandbox"])
        self.workspace_choice.SetSelection(0)
        grid.Add(self.workspace_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(parent, label=""), 0)
        self.save_creds_chk = wx.CheckBox(parent, label="Save login credentials")
        self.save_creds_chk.SetValue(True)
        grid.Add(self.save_creds_chk, 0)

        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 5)

        self.connect_btn = wx.Button(parent, label="Connect to Arena")
        self.connect_btn.Bind(wx.EVT_BUTTON, self._on_login)
        sizer.Add(self.connect_btn, 0, wx.ALL, 5)

        self.status_label = wx.StaticText(parent, label="")
        sizer.Add(self.status_label, 0, wx.ALL, 5)

    def _on_quick_connect(self, event):
        """Connect using saved credentials with selected workspace."""
        self.connect_btn.Disable()
        self.status_label.SetLabel("Connecting...")
        self.status_label.SetForegroundColour(wx.BLACK)

        from ..arena_client import ArenaClient
        workspace_label = self.quick_workspace.GetStringSelection()
        workspace_id = str(ArenaClient.WORKSPACES.get(workspace_label, ""))

        self._do_login(
            self._saved_email, self._saved_password,
            workspace_id=workspace_id,
            workspace_label=workspace_label,
            save=True,
        )

    def _on_login(self, event):
        """Connect using entered credentials."""
        email = self.email_input.GetValue().strip()
        password = self.password_input.GetValue().strip()
        if not email or not password:
            self.status_label.SetLabel("Email and password are required.")
            self.status_label.SetForegroundColour(wx.RED)
            return

        self.connect_btn.Disable()
        self.status_label.SetLabel("Connecting...")
        self.status_label.SetForegroundColour(wx.BLACK)

        from ..arena_client import ArenaClient
        workspace_label = self.workspace_choice.GetStringSelection()
        workspace_id = str(ArenaClient.WORKSPACES.get(workspace_label, ""))
        save = self.save_creds_chk.GetValue()

        self._do_login(email, password, workspace_id=workspace_id,
                       workspace_label=workspace_label, save=save)

    def _do_login(self, email, password, workspace_id=None,
                  workspace_label=None, save=True):
        """Perform Arena login in a background thread."""
        def _login():
            try:
                from ..arena_client import ArenaClient, ArenaAPI
                client = ArenaClient(
                    base_url=self.config.arena_api_url if self.config else None,
                    allow_writes=(self.config.sync_direction != "arena_to_kicad"
                                  if self.config else False),
                )
                client.login(email, password, workspace_id)

                if save and self.config:
                    self.config.arena_email = email
                    self.config.set_arena_password(password)
                    self.config.arena_workspace_id = workspace_id or ""
                    self.config.save()

                arena_api = ArenaAPI(client)
                wx.CallAfter(self._login_success, client, arena_api, workspace_label)
            except Exception as e:
                wx.CallAfter(self._login_failed, str(e))

        threading.Thread(target=_login, daemon=True).start()

    def _login_success(self, client, arena_api, workspace_label=None):
        self.client = client
        self.arena_api = arena_api
        ws = f" ({workspace_label})" if workspace_label else ""
        self.status_label.SetLabel(f"Connected as {client.user_full_name or 'user'}{ws}")
        self.status_label.SetForegroundColour(wx.Colour(0, 160, 0))
        self.connect_btn.Disable()
        self._on_connected(client, arena_api)

    def _login_failed(self, error):
        self.status_label.SetLabel(f"Login failed: {error}")
        self.status_label.SetForegroundColour(wx.RED)
        self.connect_btn.Enable()

    def _on_change_account(self, event):
        """Switch to full login form, clear saved credentials."""
        if self.config:
            self.config.arena_email = ""
            self.config.save()
        self.DestroyChildren()
        sizer = wx.StaticBoxSizer(wx.VERTICAL, self, "Arena Connection")
        self._build_full_login(sizer, sizer.GetStaticBox())
        self.SetSizer(sizer)
        self.GetParent().Layout()


# ---------------------------------------------------------------------------
# Sync Status Panel
# ---------------------------------------------------------------------------

class SyncStatusPanel(wx.Panel):
    """Compact status display showing sync state."""

    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.StaticBoxSizer(wx.VERTICAL, self, "Library Status")
        inner = sizer.GetStaticBox()

        grid = wx.FlexGridSizer(cols=4, hgap=15, vgap=4)

        self.lbl_parts = wx.StaticText(inner, label="Parts: --")
        self.lbl_last_pull = wx.StaticText(inner, label="Last pull: --")
        self.lbl_dirty = wx.StaticText(inner, label="Pending push: --")
        self.lbl_conflicts = wx.StaticText(inner, label="Conflicts: --")

        grid.Add(self.lbl_parts, 0)
        grid.Add(self.lbl_last_pull, 0)
        grid.Add(self.lbl_dirty, 0)
        grid.Add(self.lbl_conflicts, 0)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 5)
        self.SetSizer(sizer)

    def update(self, status, conflict_count=0):
        self.lbl_parts.SetLabel(f"Parts: {status.get('total_parts', 0)}")
        last = status.get("last_pull") or "Never"
        if last != "Never" and len(last) > 19:
            last = last[:19]  # Trim ISO timestamp
        self.lbl_last_pull.SetLabel(f"Last pull: {last}")
        dirty = status.get("dirty_parts", 0)
        self.lbl_dirty.SetLabel(f"Pending push: {dirty}")
        self.lbl_conflicts.SetLabel(f"Conflicts: {conflict_count}")
        if conflict_count > 0:
            self.lbl_conflicts.SetForegroundColour(wx.RED)
        else:
            self.lbl_conflicts.SetForegroundColour(wx.BLACK)


# ---------------------------------------------------------------------------
# Main Sync Dialog
# ---------------------------------------------------------------------------

class SyncDialog(wx.Dialog):
    """Main dialog for Arena PLM Library Sync.

    Layout:
    1. Arena Connection (inline login panel)
    2. Library Status (compact stats)
    3. Sync Controls (Pull / Push / Bidirectional tabs)
    4. Progress + Log
    5. Bottom buttons (Settings, View Conflicts, Close)
    """

    def __init__(self, parent, sync_client=None, config=None):
        _require_wx()
        super().__init__(parent, title="Arena PLM Library Sync",
                         size=(700, 650),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.sync_client = sync_client
        self.config = config
        self._sync_active = False
        self._arena_api = None
        self._arena_client = None

        self._build_ui()
        self._update_sync_controls_state()
        self.CenterOnScreen()

    def _build_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 1. Arena Connection (inline at top)
        self.login_panel = ArenaLoginPanel(self, self.config, self._on_connected)
        main_sizer.Add(self.login_panel, 0, wx.EXPAND | wx.ALL, 5)

        # 2. Library Status
        self.status_panel = SyncStatusPanel(self)
        main_sizer.Add(self.status_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

        # 3. Sync Controls
        self.notebook = wx.Notebook(self)
        self._build_pull_tab()
        self._build_push_tab()
        self._build_bidir_tab()
        main_sizer.Add(self.notebook, 0, wx.EXPAND | wx.ALL, 5)

        # 4. Progress + Log
        self.progress = wx.Gauge(self, range=100)
        self.progress.Hide()
        main_sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

        self.log = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
                                size=(-1, 100))
        self.log.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                  wx.FONTWEIGHT_NORMAL))
        main_sizer.Add(self.log, 1, wx.EXPAND | wx.ALL, 5)

        # 5. Bottom buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_settings = wx.Button(self, label="Settings")
        self.btn_conflicts = wx.Button(self, label="View Conflicts")
        btn_close = wx.Button(self, wx.ID_CLOSE, "Close")

        btn_sizer.Add(self.btn_settings, 0, wx.RIGHT, 5)
        btn_sizer.Add(self.btn_conflicts, 0, wx.RIGHT, 5)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(btn_close, 0)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.SetSizer(main_sizer)

        # Bindings
        self.btn_settings.Bind(wx.EVT_BUTTON, self._on_settings)
        self.btn_conflicts.Bind(wx.EVT_BUTTON, self._on_conflicts)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

    # -- Sync tabs ----------------------------------------------------------

    def _build_pull_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        mode_sizer = wx.BoxSizer(wx.HORIZONTAL)
        mode_sizer.Add(wx.StaticText(panel, label="Mode:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.pull_mode = wx.Choice(panel, choices=["Delta (changes only)", "Full (all items)"])
        self.pull_mode.SetSelection(0)
        mode_sizer.Add(self.pull_mode, 0, wx.RIGHT, 15)

        self.btn_pull = wx.Button(panel, label="Pull from Arena")
        self.btn_pull.Bind(wx.EVT_BUTTON, self._on_pull)
        mode_sizer.Add(self.btn_pull, 0)

        sizer.Add(mode_sizer, 0, wx.ALL, 10)
        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Pull from Arena")

    def _build_push_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_push = wx.Button(panel, label="Push Dirty Parts to Arena")
        self.btn_push.Bind(wx.EVT_BUTTON, self._on_push)
        sizer.Add(self.btn_push, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Push to Arena")

    def _build_bidir_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        strat_sizer = wx.BoxSizer(wx.HORIZONTAL)
        strat_sizer.Add(wx.StaticText(panel, label="Conflicts:"), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.bidir_strategy = wx.Choice(panel,
                                         choices=["Prompt user", "Arena wins", "KiCad wins"])
        self.bidir_strategy.SetSelection(0)
        strat_sizer.Add(self.bidir_strategy, 0, wx.RIGHT, 15)

        self.btn_bidir = wx.Button(panel, label="Sync Now")
        self.btn_bidir.Bind(wx.EVT_BUTTON, self._on_bidirectional)
        strat_sizer.Add(self.btn_bidir, 0)

        sizer.Add(strat_sizer, 0, wx.ALL, 10)
        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Bidirectional")

    # -- Connection callback ------------------------------------------------

    def _on_connected(self, client, arena_api):
        """Called by ArenaLoginPanel on successful login."""
        self._arena_client = client
        self._arena_api = arena_api

        # Build sync client
        from ..kicad_db import KiCadLibraryDB
        from ..sync_engine import SyncEngine, LocalSyncClient

        db_path = self.config.db_path if self.config else "/tmp/arena_library.db"
        db = KiCadLibraryDB(db_path).connect()

        engine = SyncEngine(arena_api, db, self.config)
        self.sync_client = LocalSyncClient(engine)

        self._update_sync_controls_state()
        self._refresh_status()
        self._log_message("Connected to Arena. Ready to sync.")

    def _update_sync_controls_state(self):
        """Enable/disable sync controls based on connection state."""
        connected = self.sync_client is not None
        self.btn_pull.Enable(connected)
        self.btn_push.Enable(connected)
        self.btn_bidir.Enable(connected)
        self.btn_conflicts.Enable(connected)

    def _refresh_status(self):
        if not self.sync_client:
            return
        try:
            status = self.sync_client.get_status()
            conflicts = self.sync_client.get_conflicts()
            self.status_panel.update(status, len(conflicts))
        except Exception as e:
            logger.debug("Status refresh failed: %s", e)

    # -- Sync operations ----------------------------------------------------

    def _set_sync_active(self, active):
        self._sync_active = active
        self.btn_pull.Enable(not active)
        self.btn_push.Enable(not active)
        self.btn_bidir.Enable(not active)
        if active:
            self.progress.Show()
            self.progress.SetValue(0)
        else:
            self.progress.Hide()
        self.Layout()

    def _log_message(self, msg):
        """Thread-safe log append."""
        def _append():
            self.log.AppendText(msg + "\n")
        if wx.IsMainThread():
            _append()
        else:
            wx.CallAfter(_append)

    def _progress_callback(self, current, total, part_name):
        def _update():
            if total > 0:
                pct = int(current / total * 100)
                self.progress.SetValue(pct)
            self._log_message(f"  [{current}/{total}] {part_name}")
        wx.CallAfter(_update)

    def _on_pull(self, event):
        if not self.sync_client:
            return

        mode = "full" if self.pull_mode.GetSelection() == 1 else "delta"
        self._set_sync_active(True)
        self._log_message(f"Starting {mode} pull from Arena...")
        self.sync_client.subscribe_progress(self._progress_callback)

        def _do_pull():
            try:
                result = self.sync_client.trigger_pull(mode)
                wx.CallAfter(self._on_pull_complete, result)
            except Exception as e:
                wx.CallAfter(self._on_sync_error, str(e))

        threading.Thread(target=_do_pull, daemon=True).start()

    def _on_pull_complete(self, result):
        self._set_sync_active(False)
        self._log_message(
            f"Pull complete: {result.added} added, {result.updated} updated, "
            f"{result.deleted} deleted"
        )
        if result.errors:
            for err in result.errors[:5]:
                self._log_message(f"  ERROR: {err}")
        self._refresh_status()

    def _on_push(self, event):
        if not self.sync_client:
            return

        self._set_sync_active(True)
        self._log_message("Starting push to Arena...")
        self.sync_client.subscribe_progress(self._progress_callback)

        def _do_push():
            try:
                result = self.sync_client.trigger_push()
                wx.CallAfter(self._on_push_complete, result)
            except Exception as e:
                wx.CallAfter(self._on_sync_error, str(e))

        threading.Thread(target=_do_push, daemon=True).start()

    def _on_push_complete(self, result):
        self._set_sync_active(False)
        self._log_message(
            f"Push complete: {result.created} created, {result.updated} updated, "
            f"{len(result.conflicts)} conflicts"
        )
        if result.errors:
            for err in result.errors[:5]:
                self._log_message(f"  ERROR: {err}")
        self._refresh_status()

    def _on_bidirectional(self, event):
        if not self.sync_client:
            return

        self._set_sync_active(True)
        self._log_message("Starting bidirectional sync...")
        self.sync_client.subscribe_progress(self._progress_callback)

        def _do_bidir():
            try:
                result = self.sync_client.trigger_bidirectional()
                wx.CallAfter(self._on_bidir_complete, result)
            except Exception as e:
                wx.CallAfter(self._on_sync_error, str(e))

        threading.Thread(target=_do_bidir, daemon=True).start()

    def _on_bidir_complete(self, result):
        self._set_sync_active(False)
        if isinstance(result, dict):
            pull = result.get("pull", {})
            push = result.get("push", {})
            self._log_message(
                f"Sync complete: "
                f"Pull +{getattr(pull, 'added', 0)} ~{getattr(pull, 'updated', 0)} | "
                f"Push +{getattr(push, 'created', 0)} ~{getattr(push, 'updated', 0)}"
            )
        self._refresh_status()

    def _on_sync_error(self, msg):
        self._set_sync_active(False)
        self._log_message(f"ERROR: {msg}")

    # -- Navigation ---------------------------------------------------------

    def _on_settings(self, event):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, config=self.config)
        if dlg.ShowModal() == wx.ID_OK:
            self.config = dlg.config
            self._refresh_status()
        dlg.Destroy()

    def _on_conflicts(self, event):
        if not self.sync_client:
            return
        from .conflict_dialog import ConflictDialog
        conflicts = self.sync_client.get_conflicts()
        dlg = ConflictDialog(self, conflicts=conflicts, sync_client=self.sync_client)
        dlg.ShowModal()
        dlg.Destroy()
        self._refresh_status()
