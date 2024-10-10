from __future__ import annotations

import re
import string
import sys

from dataclasses import dataclass
from enum import Enum
from getpass import getpass
from typing import Callable, Collection, Hashable, TypeVar


_T = TypeVar('_T')


def default_str_and_repr(cls):
    def __repr__(self: object):
        return '%s(%s)' % (type(self).__name__, ', '.join(['%s=%r' % (name, value) for (name, value) in vars(self).items()]))
    cls.__repr__ = __repr__
    cls.__str__ = __repr__
    return cls


def _append_if_not_in_list(__ls: list, __value: object, /) -> None:
    if __value not in __ls:
        __ls.append(__value)


class Alignment(Enum):

    LEFT = 0
    RIGHT = 1
    PYSTD = 2


class MarkingExtra(Enum):
    
    LOWER_CASE = 0
    UPPER_CASE = 1


@default_str_and_repr
class IndentOption:

    def __init__(self, indent: int | str | None, collections_to_indent: tuple[type, ...] | type | None,
                 indent_mutable_classes: bool) -> None:
        self.indent = ' ' * indent if isinstance(indent, int) else indent
        self.collections_to_indent = collections_to_indent
        self.indent_mutable_classes = indent_mutable_classes


class IndentOptions(Enum):

    NO_INDENT = IndentOption(indent=None, collections_to_indent=None, indent_mutable_classes=False)
    NORMAL_INDENT = IndentOption(indent=4, collections_to_indent=(list, tuple, dict, set), indent_mutable_classes=True)
    DICT_ONLY_INDENT = IndentOption(indent=4, collections_to_indent=dict, indent_mutable_classes=False)
    DICT_AND_CLASSES_INDENT = IndentOption(indent=4, collections_to_indent=dict, indent_mutable_classes=True)


class _BracketsRepl:
    OPEN = '\x11'
    CLOSED = '\x12'


@default_str_and_repr
class Escape:

    def __init__(self, code: int | str, fancy_io_format_key: str) -> None:
        self.code = code
        self.seq = '\x1b[%sm' % code
        self.fancy_io_format_key = fancy_io_format_key


@default_str_and_repr
class Mode:
    
    def __init__(self, name: str, desc: str, reset_mode: Mode | None) -> None:
        self.name = name
        self.desc = desc
        self.reset_mode = reset_mode
    

    def escape(self, *, for_bg: bool = False) -> Escape:
        if isinstance(self, ColorMode):
            return self.bg_esc if for_bg else self.fg_esc
        if isinstance(self, (StyleMode, ResetMode)):
            return self.esc
        return None


class ColorMode(Mode):
    
    def __init__(self, name: str, desc: str, fg_code: int, bg_code: int, fancy_io_format_key: str, reset_mode: Mode | None) -> None:
        super().__init__(name, desc, reset_mode)
        self.fg_esc = Escape(fg_code, fancy_io_format_key)
        self.bg_esc = Escape(bg_code, '#' + fancy_io_format_key)


class StyleMode(Mode):

    def __init__(self, name: str, desc: str, code: int, fancy_io_format_key: str, reset_mode: Mode | None) -> None:
        super().__init__(name, desc, reset_mode)
        self.esc = Escape(code, fancy_io_format_key)


class ResetMode(Mode):

    def __init__(self, name: str, desc: str, code: int | str, fancy_io_format_key: str, reset_mode: Mode | None) -> None:
        super().__init__(name, desc, reset_mode)
        self.esc = Escape(code, fancy_io_format_key)


@default_str_and_repr
class RgbColor:

    def __init__(self, /, red: int, green: int, blue: int) -> None:
        RgbColor._validate('Red', red)
        RgbColor._validate('Green', green)
        RgbColor._validate('Blue', blue)
        self.red = red
        self.green = green
        self.blue = blue
        self._values = (red, green, blue)


    def __getitem__(self, __i: int) -> int:
        return self._values[__i]
    

    def __setitem__(self, __key: int | slice, __value: int, /) -> None:
        self._values[__key] = __value


    # - STATISCHE METHODEN -------------------------

    @staticmethod
    def from_single_int(__int: int, /) -> RgbColor:
        assert isinstance(__int, int)
        assert __int >= 0 and __int < 256 ** 3
        red = __int >> 16
        green = (__int >> 8) % 256
        blue = __int % 256
        return RgbColor(red, green, blue)


    @staticmethod
    def from_hexstr(__hexstr: str, /) -> RgbColor:
        assert isinstance(__hexstr, str)
        assert len(__hexstr) in (6, 7)
        if len(__hexstr) == 7:
            assert __hexstr[0] == '#'
            __hexstr = __hexstr[1:]
        intval = int(__hexstr, 16) # Das eventuelle Erheben eines ValueErrors ist hier Python überlassen
        return RgbColor.from_single_int(intval)
        
    
    @staticmethod
    def _validate(name, value) -> None:
        if value not in range(256):
            raise ValueError('%s (%d) is out of range (0-255)' % (name, value))


@dataclass(kw_only=True)
class ModeCombi:

    fg: ColorMode | int | None = None
    bg: ColorMode | int | None = None
    styles: Collection[StyleMode] | StyleMode | None = None


@default_str_and_repr
class FormatTag:

    def __init__(self, /, str_value: str | None, tag_brackets: str | None = '<>') -> None:
        if tag_brackets is None:
            tag_brackets = '<>'
        assert len(tag_brackets) == 2 # Später noch mit ValueError
        self.tag_brackets = tag_brackets
        if str_value is None:
            self.str_value = tag_brackets
            self._init_empty()
        else:
            self.str_value = str_value
            self._calc_mode_attrs()
            self._calc_escseqs()


    def _calc_mode_attrs(self, /) -> None:
        if not (self.str_value.startswith(self.tag_brackets[0]) and self.str_value.endswith(self.tag_brackets[1])):
            raise ValueError("'%s': Tag does not start and end with the given brackets '%s'" % (self.str_value, self.tag_brackets))

        def set_n_256_color(*, index_inc: int = 0) -> None:
            nonlocal n_256_start, bg_color, fg_color, bg_marker
            if n_256_start is not None:
                n_256_str = tag_content[n_256_start:i + index_inc]
                (fg_color, bg_color) = self._parse_256_color(n_256_str, fg_color, bg_color, bg_marker)
                n_256_start = None
                bg_marker = False

        # Der Inhalt des Tags
        tag_content = self.str_value[1:-1]

        # Attribute, die später in das Objekt geschrieben werden
        resets: list[ResetMode] = []
        styles: list[StyleMode] = []
        bg_color: ColorMode | int | None = None
        fg_color: ColorMode | int | None = None
        resets_of_styles: list[ResetMode] = []

        if len(tag_content) > 0:
            # Boolesche Werte, die speichern, ob das vorherige Zeichen
            # ein Marker ('!' für Resets oder '#' für eine Hintergrundfarbe) war
            reset_marker: bool = False
            bg_marker: bool = False

            n_256_start: int | None = None

            # Durch den Inhalt des Tags (ohne die Klammern an den Enden) iterieren
            for (i, char) in enumerate(tag_content):
                if char == '#':
                    if reset_marker:
                        raise self._invalid_key_error('!#')
                    if bg_marker:
                        raise self._invalid_key_error('##')
                    set_n_256_color()
                    bg_marker = True
                elif char == '!':
                    if reset_marker:
                        raise self._invalid_key_error('!!')
                    if bg_marker:
                        raise self._invalid_key_error('#!')
                    set_n_256_color()
                    reset_marker = True
                elif char in ' \t,;': # Mit diesen Zeichen kann interpunktiert werden
                    if reset_marker:
                        raise self._invalid_key_error('!' + char)
                    set_n_256_color()
                    if bg_marker:
                        raise self._invalid_key_error('#' + char)
                elif char in string.digits:
                    if reset_marker:
                        raise self._invalid_key_error('!' + char)
                    if n_256_start is None:
                        n_256_start = i
                else: # bei allen Zeichen außer Ziffern, '#', '!', ' ', '\t' ',' und ';'
                    if char not in _ALL_KEY_LETTERS:
                        raise self._invalid_key_error(char)
                    set_n_256_color()
                    if bg_marker:
                        bg_color = _COLOR_MODES_FOR_FORMAT_CHARS.get(char)
                        if bg_color is None:
                            raise self._invalid_key_error('#' + char)
                        bg_marker = False
                    elif reset_marker:
                        reset_mode = _RESET_MODES_FOR_FORMAT_CHARS.get(char)
                        if reset_mode is None:
                            raise self._invalid_key_error('!', char)
                        _append_if_not_in_list(resets, reset_mode)
                        reset_marker = False
                    else:
                        style = _STYLE_MODES_FOR_FORMAT_CHARS.get(char)
                        if style is None:
                            fg_color = _COLOR_MODES_FOR_FORMAT_CHARS.get(char)
                            # if fg_color is None:
                            #     raise self._invalid_key_error(char)
                        else:
                            _append_if_not_in_list(styles, style)
                            _append_if_not_in_list(resets_of_styles, style.reset_mode)
            if tag_content[i] in '!#':
                raise self._invalid_key_error("expected key after '!' or '#'")
            if n_256_start is not None:
                set_n_256_color(index_inc=1)

        # Werte in das Objekt speichern, dabei werden Listen in Tupel umgewandelt
        self.resets: tuple[ResetMode, ...] = tuple(resets)
        self.styles: tuple[StyleMode, ...] = tuple(styles)
        self.bg_color: ColorMode | int | None = bg_color
        self.fg_color: ColorMode | int | None = fg_color
        self.resets_of_styles: tuple[ResetMode, ...] = tuple(resets_of_styles)


    def _calc_escseqs(self, /) -> None:
        self.resets_escseq: str = escseq(resets=self.resets)
        self.styles_escseq: str = escseq(styles=self.styles)
        self.bg_color_escseq: str = self._calc_color_escseq(self.bg_color, for_bg=True)
        self.fg_color_escseq: str = self._calc_color_escseq(self.fg_color, for_bg=False)
        self.resets_of_styles_escseq: str = escseq(resets=self.resets_of_styles)

        # Dafür müssen auch Wiederholungen von Schlüsseln wieder möglich sein und in die Liste aufgenommen werden
        self.full_escseq: str = escseq(resets=self.resets, fg=self.fg_color, bg=self.bg_color, styles=self.styles)

    
    def _init_empty(self, /) -> None:
        self.resets: tuple[ResetMode, ...] = ()
        self.styles: tuple[StyleMode, ...] = ()
        self.bg_color: ColorMode | int | None = None
        self.fg_color: ColorMode | int | None = None
        self.resets_of_styles: tuple[ResetMode, ...] = ()
        self.resets_escseq: str = ''
        self.styles_escseq: str = ''
        self.bg_color_escseq: str = ''
        self.fg_color_escseq: str = ''
        self.resets_of_styles_escseq: str = ''
        self.full_escseq: str = ''

    
    def _parse_256_color(self, n_256_str: str, fg_color: ColorMode | int | None, bg_color: ColorMode | int | None, bg_marker: bool) -> tuple[int, int]:
        # Prüfung, ob es sich um eine Ganzzahl handelt (mit try und except), ist nicht nötig, denn es ist sichergestellt,
        # dass alle Zeichen von n_256_start bis i + 1 Ziffern sind. Auch kann die Zahl nicht negativ sein,
        # weshalb nur die obere Grenze geprüft werden muss
        n_256_int = int(n_256_str)
        if n_256_int > 255: # 255 ist die obere Grenze für die 256 Farben
            raise self._invalid_key_error(n_256_str)
        if bg_marker:
            return (fg_color, n_256_int)
        else:
            return (n_256_int, bg_color)
        
    
    def _calc_color_escseq(self, /, color: ColorMode | int | None, *, for_bg: bool) -> str:
        if color is None:
            return ''
        return _escseq_fg_or_bg(color, for_bg, resets=self.resets, styles=self.styles)

    
    def _invalid_key_error(self, /, invalid: str) -> ValueError:
        return ValueError("'%s': invalid format key: '%s'" % (self.str_value, invalid))


@default_str_and_repr
class FormatTags:

    def __init__(self, /, tag_brackets: str = '<>', *, class_name_tag: str = '<ug>', builtin_const_tag: str = '<b>', int_or_float_tag: str = '<N>', str_tag: str = '<r>',
                 attr_name_tag: str = '<c>', enum_member_name_tag: str = '<ic>', punctuation_tag: str = '<d>', others_tag: str = '<id>') -> None:
        self.class_name_tag = FormatTag(class_name_tag, tag_brackets)
        self.builtin_const_tag = FormatTag(builtin_const_tag, tag_brackets)
        self.int_or_float_tag = FormatTag(int_or_float_tag, tag_brackets)
        self.str_tag = FormatTag(str_tag, tag_brackets)
        self.attr_name_tag = FormatTag(attr_name_tag, tag_brackets)
        self.enum_member_name_tag = FormatTag(enum_member_name_tag, tag_brackets)
        self.punctuation_tag = FormatTag(punctuation_tag, tag_brackets)
        self.others_tag = FormatTag(others_tag, tag_brackets)
    

    @staticmethod
    # Wrap in brackets
    def _wrap_in_brackets(tag_content: str, brackets: str) -> str:
        (op, cp) = brackets # open bracket, closed bracket
        return op + tag_content + cp


class CommandInputException(Exception):

    def __init__(self, command: str, whole_input: str) -> None:
        self.command = command
        self.whole_input = whole_input
        self.input_after_command = whole_input[len(command) + 1:] if len(whole_input) > len(command) else ''
        self.args = whole_input.split()
  

def _clr_desc(name: str) -> str:
    return name + ' color mode (can be used as a background and a foreground color)'


# Modi für Vordergrundfarben
DEFAULT_COLOR = ColorMode(name='DEFAULT_COLOR', desc=_clr_desc('default'), fg_code=39, bg_code=49, fancy_io_format_key='d', reset_mode=None)
BLACK = ColorMode(name='BLACK', desc=_clr_desc('black'), fg_code=30, bg_code=40, fancy_io_format_key='n', reset_mode=None)
RED = ColorMode(name='RED', desc=_clr_desc('red'), fg_code=31, bg_code=41, fancy_io_format_key='r', reset_mode=None)
GREEN = ColorMode(name='GREEN', desc=_clr_desc('green'), fg_code=32, bg_code=42, fancy_io_format_key='g', reset_mode=None)
YELLOW = ColorMode(name='YELLOW', desc=_clr_desc('yellow'), fg_code=33, bg_code=43, fancy_io_format_key='y', reset_mode=None)
BLUE = ColorMode(name='BLUE', desc=_clr_desc('blue'), fg_code=34, bg_code=44, fancy_io_format_key='b', reset_mode=None)
MAGENTA = ColorMode(name='MAGENTA', desc=_clr_desc('magenta'), fg_code=35, bg_code=45, fancy_io_format_key='m', reset_mode=None)
CYAN = ColorMode(name='CYAN', desc=_clr_desc('cyan'), fg_code=36, bg_code=46, fancy_io_format_key='c', reset_mode=None)
WHITE = ColorMode(name='WHITE', desc=_clr_desc('white'), fg_code=37, bg_code=47, fancy_io_format_key='w', reset_mode=None)
BBLACK = ColorMode(name='BBLACK', desc=_clr_desc('bright black'), fg_code=90, bg_code=100, fancy_io_format_key='N', reset_mode=None)
BRED = ColorMode(name='BRED', desc=_clr_desc('bright red'), fg_code=91, bg_code=101, fancy_io_format_key='R', reset_mode=None)
BGREEN = ColorMode(name='BGREEN', desc=_clr_desc('bright green'), fg_code=92, bg_code=102, fancy_io_format_key='G', reset_mode=None)
BYELLOW = ColorMode(name='BYELLOW', desc=_clr_desc('bright yellow'), fg_code=93, bg_code=103, fancy_io_format_key='Y', reset_mode=None)
BBLUE = ColorMode(name='BBLUE', desc=_clr_desc('bright blue'), fg_code=94, bg_code=104, fancy_io_format_key='B', reset_mode=None)
BMAGENTA = ColorMode(name='BMAGENTA', desc=_clr_desc('bright magenta'), fg_code=95, bg_code=105, fancy_io_format_key='M', reset_mode=None)
BCYAN = ColorMode(name='BCYAN', desc=_clr_desc('bright cyan'), fg_code=96, bg_code=106, fancy_io_format_key='C', reset_mode=None)
BWHITE = ColorMode(name='BWHITE', desc=_clr_desc('bright white'), fg_code=97, bg_code=107, fancy_io_format_key='W', reset_mode=None)

# Modi zum Zurücksetzten von Modi. Sie kommen vor den Styles, damit sie bei den Styles als Zurücksetzungsschlüssel angegeben werden können
RESET_ALL = ResetMode(name='RESET_ALL', desc='mode to reset all modes', code=0, reset_mode=None, fancy_io_format_key='!a')
RESET_ALL_STYLES = ResetMode(name='RESET_ALL_STYLES', desc='mode to reset all style modes', code='22;23;24;25;27;28;29', reset_mode=None, fancy_io_format_key='!s')
RESET_BOLD_DIM = ResetMode(name='RESET_BOLD_DIM', desc='mode to reset the bold and the dim style modes', code=22, reset_mode=None, fancy_io_format_key='!f')
RESET_ITALIC = ResetMode(name='RESET_ITALIC', desc='mode to reset the italic style mode', code=23, reset_mode=None, fancy_io_format_key='!i')
RESET_UNDERLINE = ResetMode(name='RESET_UNDERLINE', desc='mode to reset the underline and double underline style modes', code=24, reset_mode=None, fancy_io_format_key='!u')
RESET_BLINKING = ResetMode(name='RESET_BLINKING', desc='mode to reset the blinking style mode', code=25, reset_mode=None, fancy_io_format_key='!o')
RESET_INVERSED = ResetMode(name='RESET_INVERSED', desc='mode to reset the inversed style mode', code=27, reset_mode=None, fancy_io_format_key='!I')
RESET_HIDDEN = ResetMode(name='RESET_HIDDEN', desc='mode to reset the hidden style mode', code=28, reset_mode=None, fancy_io_format_key='!x')
RESET_STRIKETHROUGH = ResetMode(name='RESET_STRIKETHROUGH', desc='mode to reset the strikethrough style mode', code=29, reset_mode=None, fancy_io_format_key='!S')

# Modi für Styles
BOLD = StyleMode(name='BOLD', desc='bold style mode', code=1, reset_mode=RESET_BOLD_DIM, fancy_io_format_key='f')
DIM = StyleMode(name='DIM', desc='dim style mode', code=2, reset_mode=RESET_BOLD_DIM, fancy_io_format_key='D')
ITALIC = StyleMode(name='ITALIC', desc='italic style mode', code=3, reset_mode=RESET_ITALIC, fancy_io_format_key='i')
UNDERLINE = StyleMode(name='UNDERLINE', desc='underline style mode', code=4, reset_mode=RESET_UNDERLINE, fancy_io_format_key='u')
DOUBLE_UNDERLINE = StyleMode(name='DOUBLE_UNDERLINE', desc='double underline style mode', code=21, reset_mode=RESET_UNDERLINE, fancy_io_format_key='U')
BLINKING = StyleMode(name='BLINKING', desc='blinking style mode', code=5, reset_mode=RESET_BLINKING, fancy_io_format_key='o')
RAPID_BLINKING = StyleMode(name='RAPID_BLINKING', desc='rapid blinking style mode', code=6, reset_mode=RESET_BLINKING, fancy_io_format_key='O')
INVERSED = StyleMode(name='INVERSED', desc='inversed style mode', code=7, reset_mode=RESET_INVERSED, fancy_io_format_key='I')
HIDDEN = StyleMode(name='HIDDEN', desc='hidden style mode', code=8, reset_mode=RESET_HIDDEN, fancy_io_format_key='x')
STRIKETHROUGH = StyleMode(name='STRIKETHROUGH', desc='strikethrough style mode', code=9, reset_mode=RESET_STRIKETHROUGH, fancy_io_format_key='S')

# Aus den Farbmodi zusammengesetztes Tupel
COLOR_MODES = (DEFAULT_COLOR, BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, BBLACK, BRED, BGREEN, BYELLOW, BBLUE, BMAGENTA, BCYAN, BWHITE)

# Aus den Style-Modi zusammengesetztes Tupel
STYLE_MODES = (BOLD, DIM, ITALIC, UNDERLINE, DOUBLE_UNDERLINE, BLINKING, RAPID_BLINKING, INVERSED, HIDDEN, STRIKETHROUGH)

# Aus den Reset-Modi zusammengesetztes Tupel
RESET_MODES = (RESET_ALL, RESET_ALL_STYLES, RESET_BOLD_DIM, RESET_ITALIC, RESET_UNDERLINE, RESET_BLINKING, RESET_INVERSED, RESET_HIDDEN, RESET_STRIKETHROUGH)

# Aus allen Modi zusammengesetztes Tupel
ALL_MODES = COLOR_MODES + STYLE_MODES + RESET_MODES

# Dictionarys zum Mappen von Zeichen auf Modi
_COLOR_MODES_FOR_FORMAT_CHARS: dict[str, ColorMode] = {m.fg_esc.fancy_io_format_key: m for m in COLOR_MODES}
_STYLE_MODES_FOR_FORMAT_CHARS: dict[str, StyleMode] = {m.esc.fancy_io_format_key: m for m in STYLE_MODES}
_RESET_MODES_FOR_FORMAT_CHARS: dict[str, ResetMode] = {m.esc.fancy_io_format_key[1]: m for m in RESET_MODES}

_ALL_KEY_LETTERS: str = ''.join(_COLOR_MODES_FOR_FORMAT_CHARS | _STYLE_MODES_FOR_FORMAT_CHARS | _RESET_MODES_FOR_FORMAT_CHARS)


# - PRINT-FUNKTIONEN OHNE MODIFIZIERUNG

def line_breaks(__num: int, /) -> None:
    for _ in range(__num):
        print()


def print_shifted(*values: object, shift: int, sep: str | None = ' ', end: str | None = '\n') -> None:
    if shift == 0:
        print(*values, sep=sep, end=end)
        return
    if sep is None:
        sep = ' '
    if end is None:
        end = '\n'
    output = sep.join(map(str, values))
    lines = output.split('\n')
    for l in lines[:-1]:
        move_cursor_right(shift)
        print(l)
    move_cursor_right(shift)
    print(lines[-1], end=end)


# - FANCY PRINT-FUNKTIONEN ------------------

def fancyprint(*values: object, fg: ColorMode | int | None = None, bg: ColorMode | int | None = None,
               styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None, shift: int = 0,
               start_reset_all: bool = False, end_reset_all: bool = True, sep: str | None = ' ', end: str | None = '\n') -> None:
    print(_reset_all_escseq(start_reset_all, fg=fg, bg=bg, styles=styles, mode_combi=mode_combi), end='')
    print_shifted(*values, shift=shift, sep=sep, end='')
    print(_reset_all_escseq(end_reset_all), end=end)


def fancyprintf(format_str: str, tag_brackets: str | None = '<>', shift: int = 0, start_reset_all: bool = False,
                end_reset_all: bool = True, end: str | None = '\n') -> str:
    print_shifted(fancyformat(format_str, tag_brackets, start_reset_all, end_reset_all), shift=shift, end=end)


# - FANCY STRING-FUNKTIONEN ------------------

def fancystr(value: object, *, fg: ColorMode | None = None, bg: ColorMode | None = None, styles: Collection[StyleMode] | StyleMode | None = None,
             start_reset_all: bool = False, end_reset_all: bool = True) -> str:
    start_seq = _reset_all_escseq(start_reset_all, fg=fg, bg=bg, styles=styles)
    end_seq = _reset_all_escseq(end_reset_all)
    return '%s%s%s' % (start_seq, value, end_seq)


def fancystr_center(value: object, width: int, filler: str = ' ', alignment: Alignment = Alignment.RIGHT, *, fg: ColorMode | None = None,
                    bg: ColorMode | None = None, styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None,
                    modify_all: bool = False, start_reset_all: bool = False, end_reset_all: bool = True) -> str:
    string = str(value)
    if mode_combi:
        fg = mode_combi.fg
        bg = mode_combi.bg
        styles = mode_combi.styles
    if width <= len(string):
        return fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    (lspaces, rspaces) = _lrspaces(len(string), width, alignment)
    if modify_all:
        center = '%s%s%s' % (filler * lspaces, string, filler * rspaces)
        return fancystr(center, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    else:
        fcystr = fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
        return '%s%s%s' % (filler * lspaces, fcystr, filler * rspaces)
    

def fancystr_ljust(value: object, width: int, filler: str = ' ', *, fg: ColorMode | None = None, bg: ColorMode | None = None,
                   styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None,
                   modify_all: bool = False, start_reset_all: bool = False, end_reset_all: bool = True) -> str:
    string = str(value)
    if mode_combi:
        fg = mode_combi.fg
        bg = mode_combi.bg
        styles = mode_combi.styles
    spaces = width - len(string)
    if spaces <= 0:
        return fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    if modify_all:
        ljust = '%s%s' % (string, filler * spaces)
        return fancystr(ljust, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    else:
        fcystr = fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
        return '%s%s' % (fcystr, filler * spaces)
    

def fancystr_rjust(value: object, width: int, filler: str = ' ', *, fg: ColorMode | None = None, bg: ColorMode | None = None,
                   styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None,
                   modify_all: bool = False, start_reset_all: bool = False, end_reset_all: bool = True) -> str:
    string = str(value)
    if mode_combi:
        fg = mode_combi.fg
        bg = mode_combi.bg
        styles = mode_combi.styles
    spaces = width - len(string)
    if spaces <= 0:
        return fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    if modify_all:
        rjust = '%s%s' % (filler * spaces, string)
        return fancystr(rjust, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
    else:
        fcystr = fancystr(string, fg=fg, bg=bg, styles=styles, start_reset_all=start_reset_all, end_reset_all=end_reset_all)
        return '%s%s' % (filler * spaces, fcystr)


# - FANCY FORMAT-FUNKTIONEN --------------------------------------------------------------------

def fancyformat(format_str: str, tag_brackets: str | None = '<>', start_reset_all: bool = False, end_reset_all: bool = False) -> str:
    if tag_brackets is None:
        tag_brackets = '<>'
    (otb, ctb) = tag_brackets # open tag bracket, closed tag bracket
    
    # Backslashes vor alle Klammern setzen, die im String den Tag-Klammern entsprechen,
    # damit sie nicht als Teile von Tags angesehen werden
    format_str = format_str.replace('\\' + otb, _BracketsRepl.OPEN)
    format_str = format_str.replace('\\' + ctb, _BracketsRepl.CLOSED)

    _check_brackets_match(format_str, tag_brackets)

    # Diese Klammern haben eine Funktion in regulären Ausdrücken und brauchen deshalb einen Backslash vor sich
    if tag_brackets in ('()', '[]', '{}'):
        tag_re = '\\%c.*?\\%c' % (otb, ctb) # der reguläre Ausdruck, nach dem gesucht werden soll
    else:
        tag_re = '%c.*?%c' % (otb, ctb)

    format_str = _place_reset_all_tags(format_str, start_reset_all, end_reset_all)
    tags = re.findall(tag_re, format_str)
    parts = re.split(tag_re, format_str)
    escseqs = [FormatTag(t, tag_brackets).full_escseq for t in tags]
    result = ''.join(p + s for (p, s) in zip(parts, escseqs))
    result += parts[-1]
    result = result.replace(_BracketsRepl.OPEN, otb)
    result = result.replace(_BracketsRepl.CLOSED, ctb)
    return result


# - ZURÜCKSETZUNGSFUNKKTIONEN ------------------------------------------------------------------------------------------

def reset_all() -> None:
    print(RESET_ALL.esc.seq, end='')


# - STRING-FUNKTIONEN FÜR ESCAPESEQUENZEN ------------------------------------------------------------------------------

def escseq(*, resets: Collection[ResetMode] | ResetMode | None = None, fg: ColorMode | int | None = None, bg: ColorMode | int | None = None,
           styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None) -> str:
    if mode_combi is not None:
        fg = mode_combi.fg if mode_combi.fg else fg
        bg = mode_combi.bg if mode_combi.bg else bg
        styles = mode_combi.styles if mode_combi.styles else styles
    codes = _code_list(resets=resets, fg=fg, bg=bg, styles=styles)
    if len(codes) == 0:
        return ''
    else:
        return '\x1b[%sm' % ';'.join(codes)
    

def _escseq_fg_or_bg(color: ColorMode, for_bg: bool, *, resets: ResetMode | None, styles: StyleMode | None):
    if for_bg:
        return escseq(resets=resets, bg=color, styles=styles)
    else:
        return escseq(resets=resets, fg=color, styles=styles)
    

def _reset_all_escseq(reset_all: bool, *, fg: ColorMode | int | None = None, bg: ColorMode | int | None = None,
                      styles: Collection[StyleMode] | StyleMode | None = None, mode_combi: ModeCombi | None = None) -> str:
    reset_mode = RESET_ALL if reset_all else None
    return escseq(resets=reset_mode, fg=fg, bg=bg, styles=styles, mode_combi=mode_combi)


def _code_list(*, resets: Collection[ResetMode] | ResetMode | None = None, fg: ColorMode | int | None = None, bg: ColorMode | int | None = None,
               styles: Collection[StyleMode] | StyleMode | None = None) -> list[str]:
    if resets is None:
        resets = ()
    elif isinstance(resets, ResetMode):
        resets = (resets,)
    if styles is None:
        styles = ()
    elif isinstance(styles, StyleMode):
        styles = (styles,)
    codes = [str(r.esc.code) for r in resets]
    codes.extend([str(s.esc.code) for s in styles])
    if bg is not None:
        codes.append(_code_for_color_mode_or_num(bg, for_bg=True))
    if fg is not None:
        codes.append(_code_for_color_mode_or_num(fg, for_bg=False))
    return codes


def _code_for_color_mode_or_num(__color_mode_or_num: ColorMode | int, /, *, for_bg: bool) -> str:
    if isinstance(__color_mode_or_num, int):
        return '%d;5;%d' % (48 if for_bg else 38, __color_mode_or_num)
    else:
        return str(__color_mode_or_num.escape(for_bg=for_bg).code)


def unfancy(__str: str, /) -> str:
    return re.sub(r'\x1b\[.+?m', '', __str)


# - STRING-FUNKTIONEN ZUR HERVORHEBUNG VON TEXT

DEFAULT_FORMAT_TAGS = FormatTags() # Das Standard-Tag-Format für die Funktion highlighted_repr(...)
DEFAULT_HIGHLIGHT = ModeCombi(fg=BLACK, bg=YELLOW) # Die Standard-Hervorhebung


def highlight_in_str(string: str, highlight_regexes: Collection[str] | str, match_highlight: ModeCombi | None = DEFAULT_HIGHLIGHT,
                     rest_highlight: ModeCombi | None = None) -> str:
    highlight_seq = escseq(mode_combi=match_highlight)
    seq_for_rest = escseq(mode_combi=rest_highlight)
    if isinstance(highlight_regexes, str):
        regex = highlight_regexes
    else:
        regex = '|'.join(['(' + r + ')' for r in highlight_regexes])
    result = seq_for_rest
    result += re.sub(regex, lambda m: '%s%s%s%s' % (highlight_seq, m.group(), RESET_ALL.esc.seq, seq_for_rest), string)
    result += RESET_ALL.esc.seq
    return result


def highlight_escseqs(repr_str: str, highlight: ModeCombi | None = DEFAULT_HIGHLIGHT, rest_highlight: ModeCombi | None = None) -> str:
    return highlight_in_str(repr_str, r'\\x1b\[[0-9;]*?m', highlight, rest_highlight)


# - PRIVATE HILFSFUNKTIONEN ZUM PRÜFEN, OB DIE TAG-KLAMMERN IN EINEM FORMAT-STRING ÜBEREINSTIMMEN

def _check_brackets_match(format_str: str, brackets: str) -> None:
    (ob, cb) = brackets # open bracket, closed bracket
    obi = 0 # open bracket index
    opened = False
    for (i, char) in enumerate(format_str):
        if char == ob:
            if opened:
                raise _brackets_mismatch_error(format_str, obi, i, ob, cb, "Two consecutive open tag brackets '%s' are not allowed" % ob)
            obi = i
            opened = True
        elif char == cb:
            if not opened:
                raise _brackets_mismatch_error(format_str, i, None, ob, cb, "Unmatched closed tag bracket '%s'" % cb)
            opened = False
    if opened:
        raise _brackets_mismatch_error(format_str, obi, None, ob, cb, "Unclosed open tag bracket '%s'" % ob)


def _brackets_mismatch_error(format_str: str, index_1: int, index_2: int | None, open_bracket: str, closed_bracket: str, error_msg: str) -> ValueError:
    if index_2 is None:
        return _bme_one_mark(format_str, index_1, open_bracket, closed_bracket, error_msg)
    else:
        return _bme_two_marks(format_str, index_1, index_2, open_bracket, closed_bracket, error_msg)


def _bme_one_mark(format_str: str, index: int, open_bracket: str, closed_bracket: str, error_msg: str) -> None:
    index = _bme_increase_index(format_str, index)
    format_str = format_str.replace(_BracketsRepl.OPEN, '\\' + open_bracket)
    format_str = format_str.replace(_BracketsRepl.CLOSED, '\\' + closed_bracket)
    repr_index = _bme_repr_index(format_str, index)
    fsrepr = repr(format_str) # format string representation
    hlfs_lom = _bme_highlight_repr_part(fsrepr, to_index=repr_index, mark_as_error=False) # highlighted format string left of mismatch
    hlfs_rom = _bme_highlight_repr_part(fsrepr, from_index=repr_index + 1, mark_as_error=False) # right of mismatch
    hlfs_mismatch = _bme_highlight_mismatch(fsrepr, repr_index)
    hlfs = '%s%s%s' % (hlfs_lom, hlfs_mismatch, hlfs_rom) # highlighted format string
    return _finalize_bme(hlfs, error_msg)


def _bme_two_marks(format_str: str, index_1: int, index_2: int, open_bracket: str, closed_bracket: str, error_msg: str) -> None:
    index_1 = _bme_increase_index(format_str, index_1)
    index_2 = _bme_increase_index(format_str, index_2)
    format_str = format_str.replace(_BracketsRepl.OPEN, '\\' + open_bracket)
    format_str = format_str.replace(_BracketsRepl.CLOSED, '\\' + closed_bracket)
    repr_index_1 = _bme_repr_index(format_str, index_1)
    repr_index_2 = _bme_repr_index(format_str, index_2)
    fsrepr = repr(format_str) # format string representation
    hlfs_lom1 = _bme_highlight_repr_part(fsrepr, to_index=repr_index_1, mark_as_error=False) # error message left of first mismatch
    hlfs_rom2 = _bme_highlight_repr_part(fsrepr, from_index=repr_index_2 + 1, mark_as_error=False) # error message right of second mismatch
    hlfs_midp = _bme_highlight_repr_part(fsrepr, from_index=repr_index_1 + 1, to_index=repr_index_2, mark_as_error=True) # error message middle part
    hlfs_mismatch_1 = _bme_highlight_mismatch(fsrepr, repr_index_1)
    hlfs_mismatch_2 = _bme_highlight_mismatch(fsrepr, repr_index_2)
    hlfs = '%s%s%s%s%s' % (hlfs_lom1, hlfs_mismatch_1, hlfs_midp, hlfs_mismatch_2, hlfs_rom2) # highlighted format string
    return _finalize_bme(hlfs, error_msg)


def _bme_increase_index(format_str: str, index: int) -> int:
    nob = format_str[:index].count(_BracketsRepl.OPEN) # number of open brackets
    ncb = format_str[:index].count(_BracketsRepl.CLOSED) # number of closed brackets
    return index + nob + ncb


def _bme_repr_index(format_str: str, index: int) -> int:
    qubr = repr(format_str)[0] # quote used by __repr__(...)
    repr_index = 0
    for char in format_str[:index]:
        if char == qubr:
            repr_index += 2
        else:
            repr_index += len(repr(char)) - 2
    return repr_index + 1


def _bme_highlight_mismatch(fsrepr: str, index: int) -> None: # fsrepr = format string representation
    return fancystr(fsrepr[index], bg=RED)


_BME_HINT = fancystr('Hint: Use backslashes to escape tag brackets. (More coming soon)', fg=BLUE, styles=BOLD) # BME = brackets mismatch error
_BME_BRACKETS_HIGHLIGHT = ModeCombi(fg=BBLACK) # BME = brackets mismatch error
_BME_REST_HIGHLIGHT = ModeCombi(fg=CYAN)
_BME_BRACKETS_HIGHLIGHT_ERROR = ModeCombi(fg=MAGENTA, styles=UNDERLINE)
_BME_REST_HIGHLIGHT_ERROR = ModeCombi(fg=BRED, styles=UNDERLINE)

def _bme_highlight_repr_part(error_msg: str, *, from_index: int | None = None, to_index: int | None = None, mark_as_error: bool) -> None:
    if from_index is None:
        from_index = 0
    if to_index is None:
        to_index = len(error_msg)
    if mark_as_error:
        (brackets_highight, rest_highlight) = (_BME_BRACKETS_HIGHLIGHT_ERROR, _BME_REST_HIGHLIGHT_ERROR)
    else:
        (brackets_highight, rest_highlight) = (_BME_BRACKETS_HIGHLIGHT, _BME_REST_HIGHLIGHT)
    return highlight_in_str(error_msg[from_index:to_index], (r'\\\\<', r'\\\\>'), brackets_highight, rest_highlight)


def _finalize_bme(hlfs: str, error_msg: str) -> str:
    return ValueError('%s: %s\n\n%s' % (hlfs, fancystr(error_msg, fg=RED, styles=ITALIC), _BME_HINT))


# ------------------------------------

def _put_backslash_before_tag_brackets(string: str, *, brackets: str = '<>') -> str:
    (ob, cb) = brackets # open bracket, closed bracket
    string = string.replace(ob, '\\' + ob)
    string = string.replace(cb, '\\' + cb)
    return string


def _remove_backslash_before_brackets(__str: str, /, *, brackets: str = '<>') -> str:
    (ob, cb) = brackets # open bracket, closed bracket
    __str = __str.replace('\\' + ob, ob)
    __str = __str.replace('\\' + cb, cb)
    return __str


def reset_escseq(*styles: StyleMode) -> str:
    if len(styles) == 0:
        return ''
    else:
        return '\x1b[%sm' % ';'.join([str(m.reset_mode) for m in styles if m.reset_mode is not None])


def center_str(value: object, width: int, filler: str = ' ', alignment: Alignment = Alignment.RIGHT) -> str:
    string = str(value)
    (lspaces, rspaces) = _lrspaces(len(string), width, alignment)
    return '%s%s%s' % (filler * lspaces, string, filler * rspaces)


def indented_str(value: object, indent: int | str | None = 4) -> str:
    indent = _indent_to_str(indent)
    return indent + str(value)


def _indent_to_str(indent: str | int | None) -> str:
    if indent is None:
        return ''
    if isinstance(indent, int):
        return ' ' * indent
    return indent


def _lrspaces(strlen: int, width: int, alignment: Alignment) -> tuple[int, int]:
    match alignment:
        case Alignment.LEFT:
            return _lrspaces_left_aligned(strlen, width)
        case Alignment.RIGHT:
            return _lrspaces_right_aligned(strlen, width)
        case Alignment.PYSTD:
            return _lrspaces_pystd_aligned(strlen, width)


def _lrspaces_left_aligned(strlen: int, width: int) -> tuple[int, int]:
    spaces = (width - strlen)
    lspaces = spaces // 2
    rspaces = lspaces + (spaces % 2 != 0)
    return (lspaces, rspaces)


def _lrspaces_right_aligned(strlen: int, width: int) -> tuple[int, int]:
    spaces = (width - strlen)
    rspaces = spaces // 2
    lspaces = rspaces + (spaces % 2 != 0)
    return (lspaces, rspaces)


def _lrspaces_pystd_aligned(strlen: int, width: int) -> tuple[int, int]:
    floored_spaces_lr = (width - strlen) // 2
    if strlen % 2 == 0:
        return (floored_spaces_lr + (width % 2 != 0), floored_spaces_lr)
    else:
        return (floored_spaces_lr, floored_spaces_lr + (width % 2 == 0))


def _place_reset_all_tags(format_str: str, start_reset_all: bool, end_reset_all: bool) -> str:
    if start_reset_all: # and (no_check or not re.match(r'<[\s\t,;]*?!a[^>]*?>', format_str)):
        format_str = '<!a>' + format_str
    if end_reset_all: # and (no_check or not re.match(r'[^\<\>]*?\>[\s\t\,\;]*?a!.*?', format_str[::-1])):
        format_str += '<!a>'
    return format_str


def fancyformat_centered(format_str: str, width: int, alignment: Alignment = Alignment.RIGHT, filler: str = ' ',
                         tag_brackets: str | None = '<>', start_reset_all: bool = False, end_reset_all: bool = False) -> str:
    string = fancyformat(format_str, tag_brackets, start_reset_all, end_reset_all)
    length = len(unfancy(string))
    (lspaces, rspaces) = _lrspaces(length, width, alignment)
    return '%s%s%s' % (filler * lspaces, string, filler * rspaces)


def print_centered(value: object, width: int, alignment: Alignment = Alignment.RIGHT, filler: str = ' ',
                   sep: str | None = ' ', end: str | None = '\n') -> None:
    print(center_str(value, width, filler, alignment), sep=sep, end=end)


def print_indented(*values: object, indent: int | str | None = 4, not_first_line: bool = False, sep: str | None = ' ',
                   end: str | None = '\n') -> None:
    indent = _indent_to_str(indent)
    if indent == '':
        print(*values, sep=sep, end=end)
    else:
        lines = sep.join([str(v) for v in values]).split('\n')
        if len(lines) == 1:
            _print_line_indented(lines[0], '' if not_first_line else indent, end)
        else:
            _print_line_indented(lines[0], '' if not_first_line else indent, end='\n')
            for line in lines[1:-1]:
                _print_line_indented(line, indent, end='\n')
            _print_line_indented(lines[-1], indent, end)


def _print_line_indented(line: str, indent: str, end: str) -> None:
    if len(line.strip()) > 0:
        print(indent + line, end='')
    print(end=end)


def print_formatted_centered(format_str: str, width: int, alignment: Alignment = Alignment.RIGHT, tag_brackets: str | None = '<>',
                             start_reset_all: bool = False, end_reset_all: bool = False, filler: str = ' ', end: str | None = '\n') -> str:
    print(fancyformat_centered(format_str, width, alignment, filler, tag_brackets, start_reset_all, end_reset_all), end=end)


def await_enter(msg_format: str) -> None:
    getpass(fancyformat(msg_format))


def fancyinput(prompt_format: str, input_format_tag: str, *, repeat_on_empty: bool = True, commands: str | Collection[str] = (),
               exit_codes: str | Collection[str] = (), special_inputs_ignore_case: bool = False,
               call_before_exit: Callable[[], None] | None = None, validate_func: Callable[[str], str | None] | None = None,
               valid_input_marking_format_tag: str | None = None, invalid_input_marking_format_tag: str | None = None,
               command_marking_format_tag: str | None = None, exit_code_marking_format_tag: str | None = None,
               marking_extras: MarkingExtra | Collection[MarkingExtra] = ()) -> str:
    if isinstance(commands, str):
        commands = (commands,)
    if isinstance(exit_codes, str):
        exit_codes = (exit_codes,)
    if isinstance(marking_extras, MarkingExtra):
        marking_extras = (marking_extras,)
    if '' in exit_codes:
        repeat_on_empty = False
    prompt = fancyformat(prompt_format + '<!a>' + input_format_tag)
    prompt_len = len(unfancy(prompt))
    while True:
        inp = input(prompt)
        reset_all()
        if repeat_on_empty:
            while len(inp) == 0:
                inp = input(prompt)
                reset_all()
        input_command = _check_command_input(inp, commands, special_inputs_ignore_case)
        if input_command is not None:
            _mark_input(command_marking_format_tag, input_command, prompt_len)
            raise CommandInputException(input_command, inp)
        input_exit_code = _check_special_input(inp, exit_codes, special_inputs_ignore_case)
        if input_exit_code is not None:
            _mark_input(exit_code_marking_format_tag, input_exit_code, prompt_len)
            if call_before_exit is not None:
                call_before_exit()
            sys.exit()
        error_msg_format = validate_func(inp) if validate_func else None
        if error_msg_format is None:
            _mark_input(valid_input_marking_format_tag, inp, prompt_len, marking_extras)
            return inp
        _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
        fancyprintf(error_msg_format)


def fancyinput_yn(prompt_format: str, input_format_tag: str, y: str = 'Y', n: str = 'n', *, ignore_case: bool = False,
                  repeat_on_empty: bool = True, commands: str | Collection[str] = (), exit_codes: str | Collection[str] = (),
                  special_inputs_ignore_case: bool = False, invalid_input_msg_format: str | None = None,
                  call_before_exit: Callable[[], None] | None = None, y_marking_format_tag: str | None = None,
                  n_marking_format_tag: str | None = None, invalid_input_marking_format_tag: str | None = None,
                  command_marking_format_tag: str | None = None, exit_code_marking_format_tag: str | None = None) -> bool:
    if isinstance(commands, str):
        commands = (commands,)
    if isinstance(exit_codes, str):
        exit_codes = (exit_codes,)
    if '' in exit_codes:
        repeat_on_empty = False
    if invalid_input_msg_format is None:
        invalid_input_msg_format = "<i,r>Invalid input. Enter '%s' or '%s'." % (y, n)
    prompt = fancyformat(prompt_format + '<!a>' + input_format_tag)
    prompt_len = len(unfancy(prompt))
    while True:
        inp = input(prompt)
        reset_all()
        if repeat_on_empty:
            while len(inp) == 0:
                inp = input(prompt)
                reset_all()
        if _compare_input(inp, y, ignore_case):
            _mark_input(y_marking_format_tag, y, prompt_len)
            return True
        if _compare_input(inp, n, ignore_case):
            _mark_input(n_marking_format_tag, n, prompt_len)
            return False
        input_command = _check_command_input(inp, commands, special_inputs_ignore_case)
        if input_command is not None:
            _mark_input(command_marking_format_tag, input_command, prompt_len)
            raise CommandInputException(input_command, inp)
        input_exit_code = _check_special_input(inp, exit_codes, special_inputs_ignore_case)
        if input_exit_code is not None:
            _mark_input(exit_code_marking_format_tag, input_exit_code, prompt_len)
            if call_before_exit is not None:
                call_before_exit()
            sys.exit()
        _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
        fancyprintf(invalid_input_msg_format)


def _compare_input(inp: str, compare_to: str, ignore_case: bool) -> bool:
    if ignore_case:
        return inp.lower() == compare_to.lower()
    else:
        return inp == compare_to


def fancyinput_int(min: int | None, max: int | None, prompt_format: str, input_format_tag: str, *, repeat_on_empty: bool = True,
                   validate_func: Callable[[int], str | None] | None = None, commands: str | Collection[str] = (),
                   exit_codes: str | Collection[str] = (), special_inputs_ignore_case: bool = False,
                   call_before_exit: Callable[[], None] | None = None, error_msg_not_an_int: str | None = None,
                   error_msg_int_out_of_range: str | None = None, valid_input_marking_format_tag: str | None = None,
                   invalid_input_marking_format_tag: str | None = None, command_marking_format_tag: str | None = None,
                   exit_code_marking_format_tag: str | None = None) -> int:
    min_max_given = min is not None and max is not None
    if min_max_given and min > max:
        raise ValueError('min is greater than max')
    if isinstance(commands, str):
        commands = (commands,)
    if isinstance(exit_codes, str):
        exit_codes = (exit_codes,)
    if '' in exit_codes:
        repeat_on_empty = False
    if error_msg_not_an_int is None:
        error_msg_not_an_int = '<ir>Input must be an integer.<!a>'
    if min_max_given:
        if error_msg_int_out_of_range is None:
            error_msg_int_out_of_range = '<ir>Input integer out of range (%d–%d).<!a>' % (min, max)
        else:
            error_msg_int_out_of_range = error_msg_int_out_of_range.replace('\\*', _BracketsRepl.OPEN)
            error_msg_int_out_of_range = error_msg_int_out_of_range.replace('*', str(min), 1)
            error_msg_int_out_of_range = error_msg_int_out_of_range.replace('*', str(max), 1)
            error_msg_int_out_of_range = error_msg_int_out_of_range.replace(_BracketsRepl.OPEN, '*')
    prompt = fancyformat(prompt_format + '<!a>' + input_format_tag)
    prompt_len = len(unfancy(prompt))
    while True:
        inp = input(prompt)
        reset_all()
        if repeat_on_empty:
            while len(inp) == 0:
                inp = input(prompt)
                reset_all()
        input_command = _check_command_input(inp, commands, special_inputs_ignore_case)
        if input_command is not None:
            _mark_input(command_marking_format_tag, input_command, prompt_len)
            raise CommandInputException(input_command, inp)
        input_exit_code = _check_special_input(inp, exit_codes, special_inputs_ignore_case)
        if input_exit_code is not None:
            _mark_input(exit_code_marking_format_tag, input_exit_code, prompt_len)
            if call_before_exit is not None:
                call_before_exit()
            sys.exit()
        try:
            inp_int = int(inp)
        except ValueError:
            _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
            fancyprintf(error_msg_not_an_int)
            continue
        if not _input_int_in_range(inp_int, min, max):
            _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
            fancyprintf(error_msg_int_out_of_range)
            continue
        if validate_func is not None:
            error_msg = validate_func(inp_int)
            if error_msg:
                _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
                fancyprintf(error_msg)
                continue
        _mark_input(valid_input_marking_format_tag, inp, prompt_len)
        return inp_int


def fancyinput_dict_options(option_dict: dict[str, _T], prompt_format: str, input_format_tag: str, *, ignore_case: bool = False,
                            repeat_on_empty: bool = True, validate_func: Callable[[_T], str | None] | None = None,
                            commands: str | Collection[str] = (), exit_codes: str | Collection[str] = (),
                            special_inputs_ignore_case: bool = False, call_before_exit: Callable[[], None] | None = None,
                            unmatched_input_msg_format: str | None = None,
                            option_marking_format_tags: str | Collection[str] | None = None,
                            unmatched_input_marking_format_tag: str | None = None,
                            invalid_option_marking_format_tag: str | None = None,
                            command_marking_format_tag: str | None = None,
                            exit_code_marking_format_tag: str | None = None) -> _T:
    if isinstance(commands, str):
        commands = (commands,)
    if isinstance(exit_codes, str):
        exit_codes = (exit_codes,)
    if '' in option_dict.keys() or '' in exit_codes:
        repeat_on_empty = False
    if unmatched_input_msg_format is None:
        unmatched_input_msg_format = '<i,r>Invalid input. Choose one of these options: %s.<!a>' % ', '.join(map(repr, option_dict))
    prompt = fancyformat(prompt_format + '<!a>' + input_format_tag)
    prompt_len = len(unfancy(prompt))
    while True:
        inp = input(prompt)
        reset_all()
        if repeat_on_empty:
            while len(inp) == 0:
                inp = input(prompt)
                reset_all()
        input_command = _check_command_input(inp, commands, special_inputs_ignore_case)
        if input_command is not None:
            _mark_input(command_marking_format_tag, input_command, prompt_len)
            raise CommandInputException(input_command, inp)
        input_exit_code = _check_special_input(inp, exit_codes, special_inputs_ignore_case)
        if input_exit_code is not None:
            _mark_input(exit_code_marking_format_tag, input_exit_code, prompt_len)
            if call_before_exit is not None:
                call_before_exit()
            sys.exit()
        (option_key, option_value, option_index) = _fancyinput_dict_options_get_option(inp, option_dict, ignore_case)
        if option_key is None:
            _mark_input(unmatched_input_marking_format_tag, inp, prompt_len)
            fancyprintf(unmatched_input_msg_format.replace('\\*', '\x11').replace('*', inp).replace('\x11', '*'))
            continue
        if validate_func is not None:
            error_msg = validate_func(option_value)
            if error_msg:
                _mark_input(invalid_option_marking_format_tag, inp, prompt_len)
                fancyprintf(error_msg)
                continue
        if option_marking_format_tags is not None:
            omft = (option_marking_format_tags if isinstance(option_marking_format_tags, str)
                    else option_marking_format_tags[option_index])
            _mark_input(omft, option_key, prompt_len)
        return option_value


def _fancyinput_dict_options_get_option(inp: str, option_dict: dict[str, _T], ignore_case: bool) -> tuple[str, _T, int]:
    for (i, (k, v)) in enumerate(option_dict.items()):
        if (ignore_case and inp.lower() == k.lower()) or (inp == k):
            return (k, v, i)
    return (None, None, None)


def fancyinput_options(prompt_format: str, input_format_tag: str, option_formats: Collection[str],
                       return_options: Collection[_T] | None = None,
                       ignore_case: bool = False, repeat_on_empty: bool = True,
                       error_msg_format: str | None = None, commands: Collection[str] = (), exit_codes: Collection[str] = (),
                       special_inputs_ignore_case: bool = False, msg_on_exit: str | None = None,
                       invalid_input_marking_format_tag: str | None = None, command_marking_format_tag: str | None = None,
                       exit_code_marking_format_tag: str | None = None) -> _T:
    prompt = fancyformat(prompt_format + '<!a>' + input_format_tag)
    prompt_len = len(remove_format_tags(_remove_backslash_before_brackets(prompt_format)))
    options = [remove_format_tags(o) for o in option_formats]
    if return_options is None:
        return_options = options
    if '' in options or '' in exit_codes:
        repeat_on_empty = False
    if error_msg_format is None:
        error_msg_format = '<r>Invalid input. Choose one of these options: %s.<!a>' % ', '.join([repr(o) for o in options])
    while True:
        inp = input(prompt)
        reset_all()
        if repeat_on_empty:
            while len(inp) == 0:
                inp = input(prompt)
                reset_all()
        input_command = _check_command_input(inp, commands, special_inputs_ignore_case)
        if input_command is not None:
            _mark_input(command_marking_format_tag, input_command, prompt_len)
            raise CommandInputException(input_command, inp)
        input_exit_code = _check_special_input(inp, exit_codes, special_inputs_ignore_case)
        if input_exit_code is not None:
            _mark_input(exit_code_marking_format_tag, input_exit_code, prompt_len)
            if msg_on_exit is not None:
                fancyprintf(msg_on_exit)
            sys.exit()
        option_index = _find_option_index(inp, options, ignore_case)
        if option_index is None:
            _mark_input(invalid_input_marking_format_tag, inp, prompt_len)
            fancyprintf(error_msg_format)
            continue
        _mark_option(option_formats[option_index], inp, input_format_tag, prompt_len)
        return return_options[option_index]


def _input_int_in_range(inp_int: int, min: int | None, max: int | None) -> bool:
    if min is max is None:
        return True
    if min is None:
        return inp_int <= max
    if max is None:
        return inp_int >= min
    return min <= inp_int <= max


def _check_special_input(inp: str, strs: Collection[str], ignore_case: bool) -> str:
    for s in strs:
        if ignore_case:
            if inp.lower() == s.lower():
                return s
        else:
            if inp == s:
                return s
    return None


def _check_command_input(inp: str, commands: Collection[str], ignore_case: bool) -> str:
    try:
        inp_cmd = inp[:inp.index(' ')]
    except ValueError:
        inp_cmd = inp
    return _check_special_input(inp_cmd, commands, ignore_case)


def _find_option_index(inp: str, options: Collection[str], ignore_case: bool) -> int:
    for (i, o) in enumerate(options):
        if ignore_case:
            if inp.lower() == o.lower():
                return i
        else:
            if inp == o:
                return i
    return None


def _mark_option(option_format: str, inp: str, std_input_format: str, prompt_len: int) -> None:
    if not has_format_tags(option_format):
        option_format = std_input_format + option_format
    _option_replacement(option_format, inp, prompt_len)


def _mark_input(format_tag: str | None, inp: str, prompt_len: int, marking_extras: Collection[MarkingExtra] = ()) -> None:
    if format_tag is not None:
        for e in marking_extras:
            match e:
                case MarkingExtra.LOWER_CASE:
                    inp = inp.lower()
                case MarkingExtra.UPPER_CASE:
                    inp = inp.upper()
        inp, tabs = _prepare_input_marking(inp)
        move_cursor_up()
        move_cursor_right(prompt_len)
        fancyprintf(format_tag + inp, start_reset_all=True, end='')
        print(' ' * (4 * tabs))


def _prepare_input_marking(input_format: str) -> tuple[str, int]:
    result = ''
    in_tag = False
    start = 0
    tabs = 0
    for (i, char) in enumerate(input_format + '<'):
        if char == '\t':
            if not in_tag:
                tabs += 1
        elif char == '<':
            part = input_format[start:i]
            result += re.sub(r'[\x00-\x1f]', lambda c: '\\x%02x' % ord(c.group()), part)
            in_tag = True
            start = i
        elif char == '>':
            part = input_format[start:i + 1]
            result += part
            in_tag = False
            start = i + 1
    return (result, tabs)


def _option_replacement(option_format: str, prompt_len: int) -> None:
    option_string, tabs = _prepare_option_replacement(option_format)
    move_cursor_up()
    move_cursor_right(prompt_len)
    fancyprintf(option_string, start_reset_all=True, end='')
    print(' ' * (4 * tabs))


def _prepare_option_replacement(option_format: str) -> tuple[str, int]:
    result = ''
    in_tag = False
    start = 0
    tabs = 0
    # inp_start = 0
    inp_index = 0
    for (i, char) in enumerate(option_format + '<'):
        if char == '<':
            # result += _apply_case_option(option_format[start:i], inp[inp_start:inp_index])
            result += option_format[start:i]
            in_tag = True
            start = i
        elif char == '>':
            result += option_format[start:i + 1]
            in_tag = False
            start = i + 1
            # inp_start = inp_index
        elif char == '\t':
            if not in_tag:
                tabs += 1
            inp_index += 1
        else:
            if not in_tag:
                inp_index += 1
    return (result, tabs)


def fancy_format_int(__int: int, /, thousands_sep: str = ',', digit_tag: str = '<f,B>', sep_tag: str = '<N>',
                     return_as_format: bool = False) -> str:
    strint = f'{__int:,}'.replace(',', '\x11')
    strint = digit_tag + strint + '<!a>'
    strint = strint.replace('\x11', '<!a>%s%s<!a>%s' % (sep_tag, thousands_sep, digit_tag))
    if return_as_format:
        return strint
    else:
        return fancyformat(strint)
                

def move_cursor_up(lines: int = 1) -> None:
    _move_cursor('A', lines)


def move_cursor_down(lines: int = 1) -> None:
    _move_cursor('B', lines)


def move_cursor_right(places: int) -> None:
    _move_cursor('C', places)


def move_cursor_left(places: int) -> None:
    _move_cursor('D', places)


def _move_cursor(direction: str, places: int) -> None:
    if places > 0:
        print('\x1b[%d%s' % (places, direction), end='')


def has_format_tags(string: str) -> bool:
    return re.search(r'\<.*?\>', string) is not None


def remove_format_tags(format_str: str) -> str:
    format_str = format_str.replace('\\<', _BracketsRepl.OPEN)
    format_str = format_str.replace('\\>', _BracketsRepl.CLOSED)
    format_str = re.sub(r'<.+?>', '', format_str)
    format_str = format_str.replace(_BracketsRepl.OPEN, '<')
    format_str = format_str.replace(_BracketsRepl.CLOSED, '>')
    return format_str