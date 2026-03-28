"""
Main sync dialog for Arena KiCad Library Sync.

Two-panel layout:
- Left: Status panel (connection, last sync times, counts)
- Right: Tabbed sync controls (Pull / Push / Bidirectional)
- Bottom: Progress bar, scrollable log, action buttons
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


class SyncDialog(wx.Dialog if wx else object):
    """Main sync dialog for Arena PLM Library Sync."""

    def __init__(self, parent, sync_client=None, config=None):
        _require_wx()
        super().__init__(parent, title="Arena PLM Library Sync",
                         size=(800, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.sync_client = sync_client
        self.config = config
        self._sync_active = False

        self._build_ui()
        self._refresh_status()
        self.CenterOnScreen()

    def _build_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Top: two-panel layout
        top_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Left: Status panel
        status_panel = self._build_status_panel(self)
        top_sizer.Add(status_panel, 1, wx.EXPAND | wx.ALL, 5)

        # Right: Sync controls (notebook with tabs)
        self.notebook = wx.Notebook(self)
        self._build_pull_tab()
        self._build_push_tab()
        self._build_bidir_tab()
        top_sizer.Add(self.notebook, 2, wx.EXPAND | wx.ALL, 5)

        main_sizer.Add(top_sizer, 1, wx.EXPAND)

        # Bottom: Progress and log
        bottom_sizer = self._build_bottom_panel(self)
        main_sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Button row
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_settings = wx.Button(self, label="Settings")
        self.btn_conflicts = wx.Button(self, label="View Conflicts")
        self.btn_sync_log = wx.Button(self, label="Sync Log")
        btn_close = wx.Button(self, wx.ID_CLOSE, "Close")

        btn_sizer.Add(self.btn_settings, 0, wx.RIGHT, 5)
        btn_sizer.Add(self.btn_conflicts, 0, wx.RIGHT, 5)
        btn_sizer.Add(self.btn_sync_log, 0, wx.RIGHT, 5)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(btn_close, 0)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.SetSizer(main_sizer)

        # Bindings
        self.btn_settings.Bind(wx.EVT_BUTTON, self._on_settings)
        self.btn_conflicts.Bind(wx.EVT_BUTTON, self._on_conflicts)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

    def _build_status_panel(self, parent):
        panel = wx.StaticBox(parent, label="Status")
        sizer = wx.StaticBoxSizer(panel, wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, hgap=10, vgap=6)
        grid.AddGrowableCol(1)

        self.lbl_connection = wx.StaticText(panel, label="--")
        self.lbl_last_pull = wx.StaticText(panel, label="--")
        self.lbl_last_push = wx.StaticText(panel, label="--")
        self.lbl_parts_count = wx.StaticText(panel, label="--")
        self.lbl_dirty_count = wx.StaticText(panel, label="--")
        self.lbl_conflict_count = wx.StaticText(panel, label="--")
        self.lbl_deploy_mode = wx.StaticText(panel, label="--")

        labels = [
            ("Connection:", self.lbl_connection),
            ("Last pull:", self.lbl_last_pull),
            ("Last push:", self.lbl_last_push),
            ("Parts in library:", self.lbl_parts_count),
            ("Dirty (pending push):", self.lbl_dirty_count),
            ("Conflicts:", self.lbl_conflict_count),
            ("Deployment mode:", self.lbl_deploy_mode),
        ]

        for label_text, ctrl in labels:
            grid.Add(wx.StaticText(panel, label=label_text), 0, wx.ALIGN_RIGHT)
            grid.Add(ctrl, 0, wx.EXPAND)

        sizer.Add(grid, 1, wx.ALL | wx.EXPAND, 8)
        return sizer

    def _build_pull_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Pull from Arena"), 0,
                   wx.ALL, 10)

        # Mode selection
        mode_sizer = wx.BoxSizer(wx.HORIZONTAL)
        mode_sizer.Add(wx.StaticText(panel, label="Mode:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.pull_mode = wx.Choice(panel, choices=["Delta (changes only)", "Full (all items)"])
        self.pull_mode.SetSelection(0)
        mode_sizer.Add(self.pull_mode, 0)
        sizer.Add(mode_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        self.btn_pull = wx.Button(panel, label="Pull Now")
        self.btn_pull.Bind(wx.EVT_BUTTON, self._on_pull)
        sizer.Add(self.btn_pull, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "<- Pull from Arena")

    def _build_push_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Push to Arena"), 0,
                   wx.ALL, 10)

        # Source selection
        src_sizer = wx.BoxSizer(wx.HORIZONTAL)
        src_sizer.Add(wx.StaticText(panel, label="Source:"), 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.push_source = wx.Choice(panel, choices=["Dirty parts", "Schematic file"])
        self.push_source.SetSelection(0)
        src_sizer.Add(self.push_source, 0, wx.RIGHT, 10)

        self.btn_browse_sch = wx.Button(panel, label="Browse...")
        self.btn_browse_sch.Enable(False)
        src_sizer.Add(self.btn_browse_sch, 0)
        sizer.Add(src_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        self.push_source.Bind(wx.EVT_CHOICE, lambda e: self.btn_browse_sch.Enable(
            self.push_source.GetSelection() == 1))

        self.lbl_sch_path = wx.StaticText(panel, label="")
        sizer.Add(self.lbl_sch_path, 0, wx.LEFT, 10)

        self.btn_push = wx.Button(panel, label="Push Now")
        self.btn_push.Bind(wx.EVT_BUTTON, self._on_push)
        sizer.Add(self.btn_push, 0, wx.ALL, 10)

        self.btn_browse_sch.Bind(wx.EVT_BUTTON, self._on_browse_sch)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "-> Push to Arena")

    def _build_bidir_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Bidirectional Sync"), 0,
                   wx.ALL, 10)

        # Order
        order_sizer = wx.BoxSizer(wx.HORIZONTAL)
        order_sizer.Add(wx.StaticText(panel, label="Order:"), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.bidir_order = wx.Choice(panel, choices=["Pull first", "Push first"])
        self.bidir_order.SetSelection(0)
        order_sizer.Add(self.bidir_order, 0)
        sizer.Add(order_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        # Conflict strategy
        strat_sizer = wx.BoxSizer(wx.HORIZONTAL)
        strat_sizer.Add(wx.StaticText(panel, label="Conflicts:"), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.bidir_strategy = wx.Choice(panel,
                                         choices=["Prompt user", "Arena wins", "KiCad wins"])
        self.bidir_strategy.SetSelection(0)
        strat_sizer.Add(self.bidir_strategy, 0)
        sizer.Add(strat_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        self.btn_bidir = wx.Button(panel, label="Sync Now")
        self.btn_bidir.Bind(wx.EVT_BUTTON, self._on_bidirectional)
        sizer.Add(self.btn_bidir, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "<-> Bidirectional")

    def _build_bottom_panel(self, parent):
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.progress = wx.Gauge(parent, range=100)
        self.progress.Hide()
        sizer.Add(self.progress, 0, wx.EXPAND | wx.BOTTOM, 5)

        self.log = wx.TextCtrl(parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
                                size=(-1, 120))
        self.log.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                  wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.log, 0, wx.EXPAND)

        return sizer

    # -- Status refresh -----------------------------------------------------

    def _refresh_status(self):
        if self.config:
            self.lbl_deploy_mode.SetLabel(self.config.deployment_mode.title())

        if not self.sync_client:
            self.lbl_connection.SetLabel("Not connected")
            self.lbl_connection.SetForegroundColour(wx.RED)
            return

        try:
            status = self.sync_client.get_status()
            self.lbl_connection.SetLabel("Connected")
            self.lbl_connection.SetForegroundColour(wx.Colour(0, 150, 0))
            self.lbl_last_pull.SetLabel(status.get("last_pull") or "Never")
            self.lbl_last_push.SetLabel(status.get("last_push") or "Never")
            self.lbl_parts_count.SetLabel(str(status.get("total_parts", 0)))
            self.lbl_dirty_count.SetLabel(str(status.get("dirty_parts", 0)))

            conflicts = self.sync_client.get_conflicts()
            count = len(conflicts)
            self.lbl_conflict_count.SetLabel(str(count))
            if count > 0:
                self.lbl_conflict_count.SetForegroundColour(wx.RED)
            else:
                self.lbl_conflict_count.SetForegroundColour(wx.BLACK)

        except Exception as e:
            self.lbl_connection.SetLabel(f"Error: {e}")
            self.lbl_connection.SetForegroundColour(wx.RED)

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

    def _log_message(self, msg, color=None):
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
            wx.MessageBox("Not connected. Configure settings first.",
                          "Arena Sync", wx.OK | wx.ICON_WARNING)
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
            wx.MessageBox("Not connected.", "Arena Sync", wx.OK | wx.ICON_WARNING)
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
        if result.conflicts:
            self._log_message("  Open 'View Conflicts' to resolve")
        self._refresh_status()

    def _on_bidirectional(self, event):
        if not self.sync_client:
            wx.MessageBox("Not connected.", "Arena Sync", wx.OK | wx.ICON_WARNING)
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
                f"Bidirectional sync complete: "
                f"Pull: +{getattr(pull, 'added', 0)} ~{getattr(pull, 'updated', 0)} | "
                f"Push: +{getattr(push, 'created', 0)} ~{getattr(push, 'updated', 0)}"
            )
        self._refresh_status()

    def _on_sync_error(self, msg):
        self._set_sync_active(False)
        self._log_message(f"ERROR: {msg}")

    # -- Navigation ---------------------------------------------------------

    def _on_browse_sch(self, event):
        dlg = wx.FileDialog(self, "Select KiCad Schematic",
                             wildcard="KiCad Schematic (*.kicad_sch)|*.kicad_sch",
                             style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.lbl_sch_path.SetLabel(dlg.GetPath())
        dlg.Destroy()

    def _on_settings(self, event):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, config=self.config)
        if dlg.ShowModal() == wx.ID_OK:
            self.config = dlg.config
            self._refresh_status()
        dlg.Destroy()

    def _on_conflicts(self, event):
        if not self.sync_client:
            wx.MessageBox("Not connected.", "Arena Sync", wx.OK | wx.ICON_WARNING)
            return
        from .conflict_dialog import ConflictDialog
        conflicts = self.sync_client.get_conflicts()
        dlg = ConflictDialog(self, conflicts=conflicts, sync_client=self.sync_client)
        dlg.ShowModal()
        dlg.Destroy()
        self._refresh_status()
