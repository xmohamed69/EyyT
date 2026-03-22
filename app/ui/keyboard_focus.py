"""
app/ui/keyboard_focus.py
─────────────────────────────────────────────────────────────────────────────
Continuous head-position → focused key controller.

HOW IT WORKS
────────────
Instead of discrete LEFT/RIGHT/UP/DOWN commands, this controller takes a
continuous (head_x, head_y) position in [0, 1] and maps it directly to a
key in the grid:

    head_x = 0.0 → leftmost key in the row
    head_x = 1.0 → rightmost key in the row
    head_y = 0.0 → top row
    head_y = 1.0 → bottom row

This means as you tilt your head, the focused key follows your head position
continuously and immediately — no fire-and-wait, no return-to-centre gate.

Usage
─────
    fc = KeyboardFocusController.from_keyboard_widget(kb)

    # every frame:
    fc.focus_at(head_x, head_y)
    label = fc.focused_label   # → pass to DwellSelector
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.ui.keyboard_widget import KeyboardWidget

# Keep import for backward compat
try:
    from app.vision.head_navigator import HeadCommand
except ImportError:
    from enum import Enum, auto
    class HeadCommand(Enum):  # type: ignore[no-redef]
        NONE = auto(); LEFT = auto(); RIGHT = auto()
        UP = auto(); DOWN = auto()


class KeyboardFocusController:
    """
    Maintains a focused key by mapping continuous (head_x, head_y) → grid cell.

    grid[row][col] = key_label string.
    Rows are ordered top→bottom, keys within each row left→right.

    focus_at(head_x, head_y):
        head_x in [0,1] → selects column fraction within the current row
        head_y in [0,1] → selects row fraction across all rows
    """

    def __init__(
        self,
        grid: list[list[str]],
        rtl:  bool = False,
    ) -> None:
        self._grid = [row for row in grid if row]
        self._rtl  = rtl
        self._row  = 0
        self._col  = 0

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_keyboard_widget(
        cls,
        kb: "KeyboardWidget",
        rtl: bool = False,
    ) -> "KeyboardFocusController":
        """Build a grid from a live KeyboardWidget."""
        if not hasattr(kb, "_keys") or not kb._keys:
            return cls([[]], rtl=rtl)

        rows_dict: dict[int, list] = {}
        for k in kb._keys:
            rows_dict.setdefault(k.row, []).append(k)

        grid: list[list[str]] = []
        for row_idx in sorted(rows_dict.keys()):
            sorted_keys = sorted(rows_dict[row_idx], key=lambda k: k.col)
            grid.append([k.label for k in sorted_keys])

        return cls(grid, rtl=rtl)

    # ── continuous positioning ────────────────────────────────────────────────

    def focus_at(self, head_x: float, head_y: float) -> None:
        """
        Map a continuous head position to a grid cell.

        head_x : 0.0 = leftmost column,  1.0 = rightmost column
        head_y : 0.0 = top row,          1.0 = bottom row

        For RTL layouts, head_x is inverted so tilting right still feels
        natural (moves to a key further right on screen).
        """
        if not self._grid:
            return

        n_rows = len(self._grid)

        # map head_y → row index
        row = int(head_y * n_rows)
        row = max(0, min(row, n_rows - 1))

        # map head_x → col index within that row
        n_cols = len(self._grid[row])
        if self._rtl:
            # invert x for RTL so head-right → screen-right
            head_x = 1.0 - head_x
        col = int(head_x * n_cols)
        col = max(0, min(col, n_cols - 1))

        self._row = row
        self._col = col

    # ── discrete navigation (kept for backward compat) ────────────────────────

    def apply(self, cmd: "HeadCommand") -> None:
        """
        Legacy discrete navigation — still works but focus_at() is preferred.
        """
        if cmd.name == "NONE":
            return

        effective_cmd = cmd
        if self._rtl:
            if cmd.name == "LEFT":
                effective_cmd = type(cmd)["RIGHT"]  # type: ignore[index]
            elif cmd.name == "RIGHT":
                effective_cmd = type(cmd)["LEFT"]   # type: ignore[index]

        name = effective_cmd.name
        if name == "RIGHT":
            self._col = (self._col + 1) % len(self._grid[self._row])
        elif name == "LEFT":
            self._col = (self._col - 1) % len(self._grid[self._row])
        elif name == "DOWN":
            new_row = min(self._row + 1, len(self._grid) - 1)
            if new_row != self._row:
                self._row = new_row
                self._col = min(self._col, len(self._grid[self._row]) - 1)
        elif name == "UP":
            new_row = max(self._row - 1, 0)
            if new_row != self._row:
                self._row = new_row
                self._col = min(self._col, len(self._grid[self._row]) - 1)

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def focused_label(self) -> Optional[str]:
        try:
            return self._grid[self._row][self._col]
        except IndexError:
            return None

    @property
    def position(self) -> tuple[int, int]:
        return self._row, self._col

    def reset(self) -> None:
        self._row = 0
        self._col = 0

    def rebuild(self, kb: "KeyboardWidget", rtl: bool = False) -> None:
        new = KeyboardFocusController.from_keyboard_widget(kb, rtl)
        self._grid = new._grid
        self._rtl  = rtl
        self._row  = 0
        self._col  = 0