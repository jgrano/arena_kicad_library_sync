"""
Settings dialog for Arena KiCad Library Sync.

Four tabs:
1. Connection — Arena email, password, workspace, test button
2. Deployment — mode, server URL, API key, HTTP lib port, auto-sync
3. Sync Behavior — direction, conflict strategy, mechanism, paths
4. Field Mappings — editable grids for Arena<->KiCad field mapping
"""

import logging

logger = logging.getLogger(__name__)

try:
    import wx
    import wx.grid
except ImportError:
    wx = None


class SettingsDialog(wx.Dialog if wx else object):
    """Settings dialog with 4 configuration tabs."""

    def __init__(self, parent, config=None):
        if wx is None:
            raise ImportError("wxPython is required")

        super().__init__(parent, title="Arena Sync Settings",
                         size=(600, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.config = config
        self._build_ui()
        self._load_values()
        self.CenterOnScreen()

    def _build_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.notebook = wx.Notebook(self)
        self._build_connection_tab()
        self._build_deployment_tab()
        self._build_sync_tab()
        self._build_mapping_tab()

        main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_save = wx.Button(self, wx.ID_OK, "Save")
        btn_cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btn_reset = wx.Button(self, label="Reset All to Defaults")

        btn_sizer.Add(btn_reset, 0, wx.RIGHT, 10)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(btn_cancel, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_save, 0)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)

        btn_save.Bind(wx.EVT_BUTTON, self._on_save)
        btn_reset.Bind(wx.EVT_BUTTON, self._on_reset)

        self.SetSizer(main_sizer)

    # -- Tab 1: Connection --------------------------------------------------

    def _build_connection_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(panel, label="Arena Email:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_email = wx.TextCtrl(panel)
        grid.Add(self.txt_email, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Password:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        grid.Add(self.txt_password, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Workspace ID:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_workspace = wx.TextCtrl(panel)
        grid.Add(self.txt_workspace, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="API URL:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_api_url = wx.TextCtrl(panel)
        grid.Add(self.txt_api_url, 1, wx.EXPAND)

        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 15)

        self.btn_test_connection = wx.Button(panel, label="Test Connection")
        self.btn_test_connection.Bind(wx.EVT_BUTTON, self._on_test_connection)
        sizer.Add(self.btn_test_connection, 0, wx.LEFT, 15)

        self.lbl_connection_status = wx.StaticText(panel, label="")
        sizer.Add(self.lbl_connection_status, 0, wx.ALL, 15)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Connection")

    # -- Tab 2: Deployment --------------------------------------------------

    def _build_deployment_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Mode radio
        mode_box = wx.StaticBox(panel, label="Deployment Mode")
        mode_sizer = wx.StaticBoxSizer(mode_box, wx.VERTICAL)
        self.rb_local = wx.RadioButton(panel, label="Local — plugin only, no server", style=wx.RB_GROUP)
        self.rb_server = wx.RadioButton(panel, label="Server — connect to middleware")
        self.rb_both = wx.RadioButton(panel, label="Both — local + server fallback")
        mode_sizer.Add(self.rb_local, 0, wx.ALL, 4)
        mode_sizer.Add(self.rb_server, 0, wx.ALL, 4)
        mode_sizer.Add(self.rb_both, 0, wx.ALL, 4)
        sizer.Add(mode_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # Server settings
        grid = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(panel, label="Server URL:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_server_url = wx.TextCtrl(panel)
        grid.Add(self.txt_server_url, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="API Key:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_server_key = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        grid.Add(self.txt_server_key, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="HTTP lib port:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.txt_httplib_port = wx.SpinCtrl(panel, min=1024, max=65535, initial=8765)
        grid.Add(self.txt_httplib_port, 0)

        grid.Add(wx.StaticText(panel, label="Auto-sync interval:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.choice_interval = wx.Choice(panel, choices=["Off", "8 hours", "24 hours", "48 hours"])
        grid.Add(self.choice_interval, 0)

        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Deployment")

    # -- Tab 3: Sync Behavior -----------------------------------------------

    def _build_sync_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(panel, label="Default direction:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.choice_direction = wx.Choice(panel, choices=[
            "Arena to KiCad", "KiCad to Arena", "Bidirectional"])
        grid.Add(self.choice_direction, 0)

        grid.Add(wx.StaticText(panel, label="Conflict strategy:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.choice_conflict = wx.Choice(panel, choices=[
            "Prompt user", "Arena wins", "KiCad wins"])
        grid.Add(self.choice_conflict, 0)

        grid.Add(wx.StaticText(panel, label="Mechanism:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.choice_mechanism = wx.Choice(panel, choices=[
            "HTTP lib", "DB lib", "Both"])
        grid.Add(self.choice_mechanism, 0)

        grid.Add(wx.StaticText(panel, label="DB file path:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        db_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_db_path = wx.TextCtrl(panel)
        btn_db_browse = wx.Button(panel, label="Browse...")
        db_sizer.Add(self.txt_db_path, 1, wx.EXPAND | wx.RIGHT, 5)
        db_sizer.Add(btn_db_browse, 0)
        grid.Add(db_sizer, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Library output path:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        lib_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_lib_path = wx.TextCtrl(panel)
        btn_lib_browse = wx.Button(panel, label="Browse...")
        lib_sizer.Add(self.txt_lib_path, 1, wx.EXPAND | wx.RIGHT, 5)
        lib_sizer.Add(btn_lib_browse, 0)
        grid.Add(lib_sizer, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Projects root:"), 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        proj_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_proj_root = wx.TextCtrl(panel)
        btn_proj_browse = wx.Button(panel, label="Browse...")
        proj_sizer.Add(self.txt_proj_root, 1, wx.EXPAND | wx.RIGHT, 5)
        proj_sizer.Add(btn_proj_browse, 0)
        grid.Add(proj_sizer, 1, wx.EXPAND)

        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 15)

        # Browse button bindings
        btn_db_browse.Bind(wx.EVT_BUTTON, lambda e: self._browse_file(self.txt_db_path, "SQLite DB|*.db"))
        btn_lib_browse.Bind(wx.EVT_BUTTON, lambda e: self._browse_dir(self.txt_lib_path))
        btn_proj_browse.Bind(wx.EVT_BUTTON, lambda e: self._browse_dir(self.txt_proj_root))

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Sync Behavior")

    # -- Tab 4: Field Mappings ----------------------------------------------

    def _build_mapping_tab(self):
        panel = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.mapping_notebook = wx.Notebook(panel)

        # Arena -> KiCad
        self.grid_a2k = self._create_mapping_grid(self.mapping_notebook)
        self.mapping_notebook.AddPage(self.grid_a2k, "Arena -> KiCad")

        # KiCad -> Arena
        self.grid_k2a = self._create_mapping_grid(self.mapping_notebook)
        self.mapping_notebook.AddPage(self.grid_k2a, "KiCad -> Arena")

        sizer.Add(self.mapping_notebook, 1, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_add = wx.Button(panel, label="+ Add Row")
        btn_reset_map = wx.Button(panel, label="Reset to Defaults")
        btn_sizer.Add(btn_add, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_reset_map, 0)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)

        btn_add.Bind(wx.EVT_BUTTON, self._on_add_mapping_row)
        btn_reset_map.Bind(wx.EVT_BUTTON, self._on_reset_mappings)

        panel.SetSizer(sizer)
        self.notebook.AddPage(panel, "Field Mappings")

    def _create_mapping_grid(self, parent):
        grid = wx.grid.Grid(parent)
        grid.CreateGrid(0, 2)
        grid.SetColLabelValue(0, "Source Field")
        grid.SetColLabelValue(1, "Target Field")
        grid.SetColSize(0, 200)
        grid.SetColSize(1, 200)
        return grid

    def _populate_mapping_grid(self, grid, mappings):
        if grid.GetNumberRows() > 0:
            grid.DeleteRows(0, grid.GetNumberRows())
        for src, tgt in mappings.items():
            row = grid.GetNumberRows()
            grid.AppendRows(1)
            grid.SetCellValue(row, 0, src)
            grid.SetCellValue(row, 1, tgt)

    def _read_mapping_grid(self, grid):
        mappings = {}
        for row in range(grid.GetNumberRows()):
            src = grid.GetCellValue(row, 0).strip()
            tgt = grid.GetCellValue(row, 1).strip()
            if src and tgt:
                mappings[src] = tgt
        return mappings

    # -- Load / Save --------------------------------------------------------

    def _load_values(self):
        if not self.config:
            return

        d = self.config.data

        # Connection
        self.txt_email.SetValue(d["arena"]["email"])
        self.txt_workspace.SetValue(d["arena"]["workspace_id"])
        self.txt_api_url.SetValue(d["arena"]["api_url"])

        pw = self.config.get_arena_password()
        if pw:
            self.txt_password.SetValue(pw)

        # Deployment
        mode = d["deployment"]["mode"]
        if mode == "server":
            self.rb_server.SetValue(True)
        elif mode == "both":
            self.rb_both.SetValue(True)
        else:
            self.rb_local.SetValue(True)

        self.txt_server_url.SetValue(d["deployment"]["server_url"])
        key = self.config.get_server_api_key()
        if key:
            self.txt_server_key.SetValue(key)
        self.txt_httplib_port.SetValue(d["sync"]["httplib_port"])

        interval = d["sync"]["sync_interval_hours"]
        interval_map = {0: 0, 8: 1, 24: 2, 48: 3}
        self.choice_interval.SetSelection(interval_map.get(interval, 2))

        # Sync behavior
        dir_map = {"arena_to_kicad": 0, "kicad_to_arena": 1, "bidirectional": 2}
        self.choice_direction.SetSelection(dir_map.get(d["sync"]["direction"], 0))

        strat_map = {"prompt_user": 0, "arena_wins": 1, "kicad_wins": 2}
        self.choice_conflict.SetSelection(strat_map.get(d["sync"]["conflict_strategy"], 0))

        mech_map = {"http_lib": 0, "db_lib": 1, "both": 2}
        self.choice_mechanism.SetSelection(mech_map.get(d["sync"]["mechanism"], 2))

        self.txt_db_path.SetValue(d["sync"]["db_path"])
        self.txt_lib_path.SetValue(d.get("kicad_library_path", ""))
        self.txt_proj_root.SetValue(d.get("kicad_projects_root", ""))

        # Field mappings
        self._populate_mapping_grid(self.grid_a2k, d["field_mappings"]["arena_to_kicad"])
        self._populate_mapping_grid(self.grid_k2a, d["field_mappings"]["kicad_to_arena"])

    def _on_save(self, event):
        if not self.config:
            self.EndModal(wx.ID_OK)
            return

        d = self.config.data

        # Connection
        d["arena"]["email"] = self.txt_email.GetValue()
        d["arena"]["workspace_id"] = self.txt_workspace.GetValue()
        d["arena"]["api_url"] = self.txt_api_url.GetValue()

        pw = self.txt_password.GetValue()
        if pw:
            self.config.set_arena_password(pw)

        # Deployment
        if self.rb_server.GetValue():
            d["deployment"]["mode"] = "server"
        elif self.rb_both.GetValue():
            d["deployment"]["mode"] = "both"
        else:
            d["deployment"]["mode"] = "local"

        d["deployment"]["server_url"] = self.txt_server_url.GetValue()
        key = self.txt_server_key.GetValue()
        if key:
            self.config.set_server_api_key(key)
        d["sync"]["httplib_port"] = self.txt_httplib_port.GetValue()

        interval_vals = [0, 8, 24, 48]
        d["sync"]["sync_interval_hours"] = interval_vals[self.choice_interval.GetSelection()]

        # Sync behavior
        dir_vals = ["arena_to_kicad", "kicad_to_arena", "bidirectional"]
        d["sync"]["direction"] = dir_vals[self.choice_direction.GetSelection()]

        strat_vals = ["prompt_user", "arena_wins", "kicad_wins"]
        d["sync"]["conflict_strategy"] = strat_vals[self.choice_conflict.GetSelection()]

        mech_vals = ["http_lib", "db_lib", "both"]
        d["sync"]["mechanism"] = mech_vals[self.choice_mechanism.GetSelection()]

        d["sync"]["db_path"] = self.txt_db_path.GetValue()
        d["kicad_library_path"] = self.txt_lib_path.GetValue()
        d["kicad_projects_root"] = self.txt_proj_root.GetValue()

        # Field mappings
        d["field_mappings"]["arena_to_kicad"] = self._read_mapping_grid(self.grid_a2k)
        d["field_mappings"]["kicad_to_arena"] = self._read_mapping_grid(self.grid_k2a)

        # Validate and save
        issues = self.config.validate()
        if issues:
            wx.MessageBox("Configuration issues:\n\n" + "\n".join(issues),
                          "Validation", wx.OK | wx.ICON_WARNING)
            return

        self.config.save()
        self.EndModal(wx.ID_OK)

    def _on_reset(self, event):
        if wx.MessageBox("Reset all settings to defaults?", "Confirm",
                         wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            from ..config import DEFAULT_CONFIG
            from copy import deepcopy
            self.config._data = deepcopy(DEFAULT_CONFIG)
            self._load_values()

    def _on_test_connection(self, event):
        email = self.txt_email.GetValue()
        password = self.txt_password.GetValue()
        workspace = self.txt_workspace.GetValue()
        api_url = self.txt_api_url.GetValue()

        if not email or not password:
            self.lbl_connection_status.SetLabel("Email and password required")
            self.lbl_connection_status.SetForegroundColour(wx.RED)
            return

        self.lbl_connection_status.SetLabel("Connecting...")
        self.lbl_connection_status.SetForegroundColour(wx.BLACK)
        self.Update()

        try:
            from ..arena_client import ArenaClient
            client = ArenaClient(base_url=api_url)
            client.login(email, password, workspace)
            client.logout()
            self.lbl_connection_status.SetLabel("Connected successfully!")
            self.lbl_connection_status.SetForegroundColour(wx.Colour(0, 150, 0))
        except Exception as e:
            self.lbl_connection_status.SetLabel(f"Failed: {e}")
            self.lbl_connection_status.SetForegroundColour(wx.RED)

    # -- Helpers ------------------------------------------------------------

    def _on_add_mapping_row(self, event):
        active_tab = self.mapping_notebook.GetSelection()
        grid = self.grid_a2k if active_tab == 0 else self.grid_k2a
        grid.AppendRows(1)

    def _on_reset_mappings(self, event):
        from ..config import DEFAULT_CONFIG
        self._populate_mapping_grid(
            self.grid_a2k, DEFAULT_CONFIG["field_mappings"]["arena_to_kicad"])
        self._populate_mapping_grid(
            self.grid_k2a, DEFAULT_CONFIG["field_mappings"]["kicad_to_arena"])

    def _browse_file(self, text_ctrl, wildcard="All files|*.*"):
        dlg = wx.FileDialog(self, wildcard=wildcard, style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            text_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _browse_dir(self, text_ctrl):
        dlg = wx.DirDialog(self, style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            text_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()
