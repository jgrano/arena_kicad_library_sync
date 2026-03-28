"""
Conflict resolution dialog for Arena KiCad Library Sync.

Split view:
- Left 40%: Part list with status indicators
- Right 60%: Detail with side-by-side Arena/KiCad values
- Bottom: Resolution buttons and auto-resolve options
"""

import logging

logger = logging.getLogger(__name__)

try:
    import wx
except ImportError:
    wx = None


class ConflictDialog(wx.Dialog if wx else object):
    """Dialog for reviewing and resolving sync conflicts."""

    def __init__(self, parent, conflicts=None, sync_client=None):
        if wx is None:
            raise ImportError("wxPython is required")

        super().__init__(parent, title="Resolve Conflicts",
                         size=(750, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.conflicts = conflicts or []
        self.sync_client = sync_client
        self._selected_idx = -1

        self._build_ui()
        self._populate_list()
        self.CenterOnScreen()

    def _build_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Split: list + detail
        splitter = wx.BoxSizer(wx.HORIZONTAL)

        # Left: conflict list
        self.conflict_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.conflict_list.InsertColumn(0, "Part Number", width=120)
        self.conflict_list.InsertColumn(1, "Field", width=100)
        self.conflict_list.InsertColumn(2, "Status", width=80)
        self.conflict_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        splitter.Add(self.conflict_list, 2, wx.EXPAND | wx.ALL, 5)

        # Right: detail panel
        detail_panel = wx.Panel(self)
        detail_sizer = wx.BoxSizer(wx.VERTICAL)

        self.lbl_part_info = wx.StaticText(detail_panel, label="Select a conflict")
        detail_sizer.Add(self.lbl_part_info, 0, wx.ALL, 5)

        # Comparison grid
        self.detail_grid = wx.FlexGridSizer(cols=3, hgap=10, vgap=8)
        self.detail_grid.AddGrowableCol(1)
        self.detail_grid.AddGrowableCol(2)

        self.detail_grid.Add(wx.StaticText(detail_panel, label=""), 0)
        self.detail_grid.Add(wx.StaticText(detail_panel, label="Arena Value"), 0, wx.ALIGN_CENTER)
        self.detail_grid.Add(wx.StaticText(detail_panel, label="KiCad Value"), 0, wx.ALIGN_CENTER)

        self.lbl_field_name = wx.StaticText(detail_panel, label="")
        self.lbl_arena_value = wx.TextCtrl(detail_panel, style=wx.TE_READONLY)
        self.lbl_kicad_value = wx.TextCtrl(detail_panel, style=wx.TE_READONLY)

        self.detail_grid.Add(self.lbl_field_name, 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self.detail_grid.Add(self.lbl_arena_value, 1, wx.EXPAND)
        self.detail_grid.Add(self.lbl_kicad_value, 1, wx.EXPAND)

        detail_sizer.Add(self.detail_grid, 0, wx.EXPAND | wx.ALL, 10)

        # Timestamps
        self.lbl_arena_modified = wx.StaticText(detail_panel, label="")
        self.lbl_local_modified = wx.StaticText(detail_panel, label="")
        detail_sizer.Add(self.lbl_arena_modified, 0, wx.LEFT, 10)
        detail_sizer.Add(self.lbl_local_modified, 0, wx.LEFT, 10)

        # Recommendation
        self.lbl_recommended = wx.StaticText(detail_panel, label="")
        self.lbl_recommended.SetFont(
            wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
        detail_sizer.Add(self.lbl_recommended, 0, wx.ALL, 10)

        # Resolution buttons
        res_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_use_arena = wx.Button(detail_panel, label="<- Use Arena")
        self.btn_use_kicad = wx.Button(detail_panel, label="Use KiCad ->")
        self.btn_use_arena.Enable(False)
        self.btn_use_kicad.Enable(False)
        res_sizer.Add(self.btn_use_arena, 0, wx.RIGHT, 10)
        res_sizer.Add(self.btn_use_kicad, 0)
        detail_sizer.Add(res_sizer, 0, wx.ALL, 10)

        self.btn_use_arena.Bind(wx.EVT_BUTTON, self._on_use_arena)
        self.btn_use_kicad.Bind(wx.EVT_BUTTON, self._on_use_kicad)

        detail_panel.SetSizer(detail_sizer)
        splitter.Add(detail_panel, 3, wx.EXPAND | wx.ALL, 5)

        main_sizer.Add(splitter, 1, wx.EXPAND)

        # Bottom: auto-resolve + close
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.auto_resolve_choice = wx.Choice(
            self, choices=["Arena wins", "KiCad wins"])
        self.auto_resolve_choice.SetSelection(0)
        btn_auto = wx.Button(self, label="Auto-resolve All")
        btn_close = wx.Button(self, wx.ID_CLOSE, "Close")

        bottom_sizer.Add(wx.StaticText(self, label="Auto-resolve:"), 0,
                         wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        bottom_sizer.Add(self.auto_resolve_choice, 0, wx.RIGHT, 5)
        bottom_sizer.Add(btn_auto, 0, wx.RIGHT, 20)
        bottom_sizer.AddStretchSpacer()
        bottom_sizer.Add(btn_close, 0)

        main_sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.ALL, 10)

        btn_auto.Bind(wx.EVT_BUTTON, self._on_auto_resolve)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

        self.SetSizer(main_sizer)

    def _populate_list(self):
        self.conflict_list.DeleteAllItems()
        for i, c in enumerate(self.conflicts):
            idx = self.conflict_list.InsertItem(i, c.arena_number)
            self.conflict_list.SetItem(idx, 1, c.field_name)
            status = "Resolved" if c.resolution else "Pending"
            self.conflict_list.SetItem(idx, 2, status)

    def _on_select(self, event):
        self._selected_idx = event.GetIndex()
        if 0 <= self._selected_idx < len(self.conflicts):
            c = self.conflicts[self._selected_idx]
            self.lbl_part_info.SetLabel(f"{c.arena_number} — {c.description}")
            self.lbl_field_name.SetLabel(c.field_name)
            self.lbl_arena_value.SetValue(c.arena_value)
            self.lbl_kicad_value.SetValue(c.local_value)
            self.lbl_arena_modified.SetLabel(
                f"Arena modified: {c.arena_modified_at or 'Unknown'}")
            self.lbl_local_modified.SetLabel(
                f"Local modified: {c.local_modified_at or 'Unknown'}")
            self.lbl_recommended.SetLabel(f"Recommended: {c.recommended}")

            resolved = c.resolution is not None
            self.btn_use_arena.Enable(not resolved)
            self.btn_use_kicad.Enable(not resolved)

    def _resolve(self, resolution):
        if self._selected_idx < 0:
            return
        c = self.conflicts[self._selected_idx]
        if self.sync_client:
            try:
                self.sync_client.resolve_conflict(c.arena_guid, c.field_name, resolution)
                c.resolution = resolution
                self._populate_list()
                self.btn_use_arena.Enable(False)
                self.btn_use_kicad.Enable(False)
            except Exception as e:
                wx.MessageBox(f"Failed to resolve: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def _on_use_arena(self, event):
        self._resolve("arena_wins")

    def _on_use_kicad(self, event):
        self._resolve("kicad_wins")

    def _on_auto_resolve(self, event):
        strategy = "arena_wins" if self.auto_resolve_choice.GetSelection() == 0 else "kicad_wins"
        for c in self.conflicts:
            if c.resolution is None and self.sync_client:
                try:
                    self.sync_client.resolve_conflict(c.arena_guid, c.field_name, strategy)
                    c.resolution = strategy
                except Exception:
                    pass
        self._populate_list()
