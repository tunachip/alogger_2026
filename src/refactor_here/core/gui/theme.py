from enum import StrEnum, IntEnum

class Theme(StrEnum):
    USER = 'unset'
    BASE = 'clam'

class Color(StrEnum):
    BLACK   = '#000000'
    WHITE   = '#ffffff'
    GRAY    = '#999999'
    RED     = '#ff0000'
    GREEN   = '#00ff00'
    BLUE    = '#0000ff'
    YELLOW  = '#ffff00'
    CYAN    = '#00ffff'
    MAGENTA = '#ff00ff'

    GREY_01 = '#161616'
    GREY_02 = '#323232'
    GREY_03 = '#646464'
    GREY_04 = '#999999'
    GREY_05 = '#dddddd'
    
    DIM_YELLOW = '#dddd00'

class Colorscheme(StrEnum):
    # === Main ===
    MAIN_BG            = Color.GREY_01
    MAIN_FG            = Color.WHITE
    MAIN_ACCENT        = Color.YELLOW
    MAIN_BORDER        = Color.GREY_02
    # === Player Pane ===
    PLAYER_BG          = Color.BLACK
    PLAYER_FG          = Color.WHITE
    PLAYER_ACCENT      = Color.YELLOW
    PLAYER_BORDER      = Color.GREY_02
    # === Menu Header ===
    MENU_HEADER_BG     = Color.GREY_01
    MENU_HEADER_FG     = Color.GREY_04
    # === Side Menu ===
    SIDE_MENU_BG       = Color.GREY_01
    SIDE_MENU_FG       = Color.GREY_05
    SIDE_MENU_ACCENT   = Color.YELLOW
    SIDE_MENU_BORDER   = Color.GREY_02
    # === Popup Menu ===
    POPUP_MENU_BG      = Color.GREY_01
    POPUP_MENU_FG      = Color.WHITE
    POPUP_MENU_ACCENT  = Color.YELLOW
    POPUP_MENU_BORDER  = Color.GREY_02
    # === Text Field ===
    TEXT_FIELD_BG      = Color.GREY_01
    TEXT_FIELD_FG      = Color.WHITE
    TEXT_FIELD_ACCENT  = Color.YELLOW
    TEXT_FIELD_BORDER  = Color.GREY_02
    # === Status Bar ===
    STATUS_BASE_BG     = Color.BLACK
    STATUS_BASE_FG     = Color.GREY_05
    STATUS_BASE_ACCENT = Color.YELLOW
    STATUS_BASE_BORDER = Color.GREY_02
    STATUS_CONFIRM_BG  = Color.YELLOW
    STATUS_CONFIRM_FG  = Color.BLACK
    STATUS_SUCCESS_BG  = Color.GREEN
    STATUS_SUCCESS_FG  = Color.WHITE
    STATUS_FAILURE_BG  = Color.RED
    STATUS_FAILURE_FG  = Color.WHITE
    # === Caption View ===
    CAPTION_TIME_FG    = Color.GREY_04
    CAPTION_BASE_FG    = Color.GREY_05
    CAPTION_BASE_BG    = Color.GREY_01
    CAPTION_BASE_ACCENT= Color.DIM_YELLOW
    CAPTION_MATCH_FG   = Color.YELLOW
    CAPTION_SELECT_FG  = Color.WHITE
    CAPTION_SELECT_BG  = Color.GREY_02

class Geometry(IntEnum):
    MAIN_X              = 1600
    MAIN_Y              = 800
    FONT_SIZE           = 14
    LEFT_PANEL_MIN_SIZE = 200
    SIDE_MENU_MIN_SIZE  = 200

class Font(StrEnum):
    USER     = 'unset'
    BASE     = 'DejaVu Sans Mono'
    FALLBACK = 'monospace'

