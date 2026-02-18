import colorsys
from enum import Enum
from dataclasses import dataclass

class Color(Enum):
    BLACK   = '#000000'
    WHITE   = '#ffffff'
    GRAY    = '#999999'
    RED     = '#ff0000'
    GREEN   = '#00ff00'
    BLUE    = '#0000ff'
    YELLOW  = '#ffff00'
    CYAN    = '#00ffff'
    MAGENTA = '#ff00ff'

class Colorscheme(Enum):
    # === Main ===
    MAIN_BG            = '#111111'
    MAIN_FG            = '#ffffff'
    MAIN_ACCENT        = '#ffff00'
    MAIN_BORDER        = '#323232'
    # === Player Pane ===
    PLAYER_BG          = '#000000'
    PLAYER_FG          = '#ffffff'
    PLAYER_ACCENT      = '#ffff00'
    PLAYER_BORDER      = '#323232'
    # === Menu Header ===
    MENU_HEADER_BG     = '#161616'
    MENU_HEADER_FG     = '#999999'
    # === Side Menu ===
    SIDE_MENU_BG       = '#161616'
    SIDE_MENU_FG       = '#dddddd'
    SIDE_MENU_ACCENT   = '#ffff00'
    SIDE_MENU_BORDER   = '#323232'
    # === Popup Menu ===
    POPUP_MENU_BG      = '#161616'
    POPUP_MENU_FG      = '#ffffff'
    POPUP_MENU_ACCENT  = '#ffff00'
    POPUP_MENU_BORDER  = '#323232'
    # === Text Field ===
    TEXT_FIELD_BG      = '#161616'
    TEXT_FIELD_FG      = '#f0f0f0'
    TEXT_FIELD_ACCENT  = '#ffff00'
    TEXT_FIELD_BORDER  = '#323232'
    # === Status Bar ===
    STATUS_BASE_BG     = '#000000'
    STATUS_BASE_FG     = '#dddddd'
    STATUS_BASE_ACCENT = '#dddd00'
    STATUS_BASE_BORDER = '#323232'
    STATUS_CONFIRM_BG  = '#999900'
    STATUS_CONFIRM_FG  = '#ffffff'
    STATUS_SUCCESS_BG  = '#009900'
    STATUS_SUCCESS_FG  = '#ffffff'
    STATUS_FAILURE_BG  = '#990000'
    STATUS_FAILURE_FG  = '#ffffff'
    # === Caption View ===
    CAPTION_TIME_FG    = '#999999'
    CAPTION_BASE_FG    = '#dddddd'
    CAPTION_BASE_BG    = '#111111'
    CAPTION_MATCH_FG   = '#ffff00'
    CAPTION_SELECT_FG  = '#ffffff'
    CAPTION_SELECT_BG  = '#323232'

class FontFamily(Enum):
    USER = None
    BASE = 'DejaVu Sans Mono'
    FALLBACK = 'monospace'

@dataclass(slots=True)
class SegmentRow:
    index: int
    start: float
    end:   float
    text:    str
    text_lc: str


