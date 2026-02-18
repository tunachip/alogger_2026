from dataclasses import dataclass

@dataclass(slots=True)
class SegmentRow:
    index:   int
    start:   float
    end:     float
    text:    str
    text_lc: str

@dataclass(slots=True)
class TextStyle:
    font:  str
    size:  int
    style: str

@dataclass(slots=True)
class TextBoxStyle:
    fg: str
    bg: str
    text_style: TextStyle

@dataclass(slots=True)
class CaptionStyle:
    name: str
    font: str
    fg:   str
    bg:   str

PLAYER = {
    'title': 'Alog Player',
    'geometry': '1600x1020',
    'style': {'bg': '#111111'}
}


