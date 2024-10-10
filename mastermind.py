# Mastermind von Julian Hölz


import os
import random
import re

from fancy_io import * # Meine eigene Bibliothek fancy_io wird importiert für modifizierte Eingaben


class Color:
    
    def __init__(self, name: str, color: ColorMode) -> None:
        self.name = name
        self.letter = name[0]
        self.color = color
        
        
    def highlighted_name(self, highlight_first_letter: bool) -> str:
        if highlight_first_letter:
            return fancystr(self.name[0], fg=self.color, styles=BOLD) + fancystr(self.name[1:], fg=self.color)
        else:
            return fancystr(self.name, fg=self.color)


class Colors:
    
    BOARD_BG = 90
    BOARD_BG_MARKED = 164
    BOARD_LINES = 213
    BOARD_LINES_MARKED = WHITE
    BOARD_HOLES = 53
    BOARD_HOLES_MARKED = BOARD_BG


TITLE = """ _____         _                 _       _
|     |___ ___| |_ ___ ___ _____|_|___ _| |
| | | | .'|_ -|  _| -_|  _|     | |   | . |
|_|_|_|__,|___|_| |___|_| |_|_|_|_|_|_|___|
==========================================="""

LENGTH = 7 # die Länge des Spielbrettes (so viele Versuche hat der Spieler)
N_BOARD_LINES = 3 * (LENGTH + 1) + 1 # So viele Zeilen hat das Brett im Terminal

CORRECT_POSITION = 1
CORRECT_COLOR = 2

KEEP = 4
DELETE = 5

C1 = Color('Rot', RED)
C2 = Color('Grün', GREEN)
C3 = Color('Blau', BLUE)
C4 = Color('Weiß', WHITE)
C5 = Color('Schwarz', BLACK)
C6 = Color('Cyan', CYAN)

COLORS = (C1, C2, C3, C4, C5, C6)
AVAILABLE_INPUT_LETTERS = tuple([c.name[0] for c in COLORS]) + ('X', '-')
LETTER_COLOR_MAPPING = {c: k for (c, k) in zip(AVAILABLE_INPUT_LETTERS, COLORS)} | {'-': KEEP, 'X': DELETE}

INVALID_INPUT_MSG = '<r>Ungültige Eingabe. Gib »<f,c>/Hilfe<!f,r>« ein, um die Spielanleitung ausgegeben zu bekommen.'
COMMANDS = ('/Hilfe',)
EXIT_CODES = ('/Ende',) # "/Ende" soll der Spieler eingeben können, um das Programm zu beenden

# Mit diesen Format-Tags werden Eingaben markiert. Der Name sagt, was für Eingaben.
VALID_INPUT_MARKING_FORMAT_TAG = '<f,c>'
INVALID_INPUT_MARKING_FORMAT_TAG = '<r>'
COMMAND_MARKING_FORMAT_TAG = '<f,c>'
EXIT_CODE_MARKING_FORMAT_TAG = '<f,r>'
Y_MARKING_FORMAT = '<f,g>'
N_MARKING_FORMAT = '<f,r>'

GAME_INSTRUCTIONS = """
<fU,c>Spielanleitung<!a>

<fu,c>Ziel des Spiels<!a>

<b>Das Ziel des Spiels ist es, den Code, den der Computer zu Beginn des Spiels
zufällig generiert, zu knacken. Dazu hast du %d Versuche.

Der Code besteht aus 4 Kugeln, welche jeweils eine von %d Farben haben. Die
Farben sind: %s<b> und %s<b>.

Beim Knacken des Codes helfen dir rote und weiße Stecknadeln. Was diese
bedeuten, wird weiter unten erklärt.<!a>

<fu,c>Ablauf des Spiels<!a>

<b>Bis der Code geknackt ist oder du deine Versuche aufgebraucht hast, wirst du
aufgefordert, Tipps auf den Code einzugeben.

Wenn du einen Tipp abgegeben hast, erscheinen rechts eventuell rote und/oder
weiße Stecknadeln. Eine rote Stecknadel bedeutet, dass eine Farbe korrekt und an
der korrekten Position ist. Eine weiße Stecknadel bedeutet, dass eine Farbe
korrekt, aber nicht an der korrekten Position ist.

Wenn du den Code geknackt hast, hast du das Spiel gewonnen. Wenn du alle
deine Versuche aufgebraucht haben und der Code nicht geknackt ist, hast du das
Spiel verloren.<!a>

<fu,c>Eingabe von Tipps<!a>

<b>Bei der Eingabeaufforderung hast du zwei Möglichkeiten:

  1. Du kannst einen Tipp auf den ganzen Code abgeben. Dazu gibst du die
     Anfangsbuchstaben von 4 Farben ein. Groß- und Kleinschreibung spielt
     dabei keine Rolle. Beispiel-Eingabe: »<f,c>BRRW<!f,b>«

  2. Du kannst eine einzelne Farbe setzen. Die Syntax dafür ist:
     <N>\\<<f,c>Spalte<!f,N>\\>\\<<f,c>Anfangsbuchstabe der Farbe<!f,N>\\><b>. Beispiel-Eingabe: »<f,c>3R<!f,b>«
     
Du kannst zudem Leerzeichen und Kommata eingeben, um die Eingabe
übersichtlicher zu machen.

Ein Bindestrich in deiner Eingabe steht dafür, dass die Farbe an dieser Stelle
beibehalten wird. Ein »x« in deiner Eingabe steht dafür, dass die Farbe an dieser
Stelle gelöscht wird.

Die Eingabe wird erst gezählt, wenn alle Farben in der Reihe gesetzt sind.
"""[1:-1] % (LENGTH, len(COLORS), '<b>, '.join(k.highlighted_name(False) for k in COLORS[:-1]), COLORS[-1].highlighted_name(False))

N_GAME_INSTRUCTIONS_LINES = GAME_INSTRUCTIONS.count('\n') + 1

EXAMPLE_BOARD_ROWS = [[C3, C3, C4, C4], [C3, C3, C5, C5], [C2, C5, C6, C4],
                      [C2, C5, C1, C4], [None, C5, None, C4]] + [[None] * 4 for _ in range(LENGTH - 5)]
EXAMPLE_BOARD_PINS = [[CORRECT_POSITION], [CORRECT_COLOR], [CORRECT_POSITION, CORRECT_POSITION, CORRECT_POSITION],
                      [CORRECT_POSITION, CORRECT_POSITION, CORRECT_COLOR]] + [[]] * (LENGTH - 4)


current_guess: list[Color | None] | tuple[int, Color] | None = None
confirm_guess = False


# Dies ist die main()-Methode. Sie wird bei Programmstart aufgerufen
def main() -> None:
    play_one_game()
    while input_play_again():
        play_one_game()
    print_program_ended()


# In dieser Methode wird einmal Mastermind gespielt
def play_one_game() -> None:
    global current_guess, confirm_guess
    solution_code = random_code()
    rows = [[None] * 4 for _ in range(LENGTH)]
    pins = [[]] * LENGTH
    n_codes_guessed = 0
    show_hint_confirmation_not_possible = False
    while True:
        print_all(rows, pins, None, marked_row=n_codes_guessed)
        if show_hint_confirmation_not_possible:
            fancyprint('Der Tipp kann nicht bestätigt werden, da die Reihe nicht gefüllt ist.', fg=RED)
            line_breaks(1)
            show_hint_confirmation_not_possible = False
        try:
            input_guess()
        except CommandInputException:
            print_game_instructions()
            continue
        if isinstance(current_guess, list):
            for (i, g) in enumerate(current_guess):
                set_tile(rows[n_codes_guessed], i, g)
        elif isinstance(current_guess, tuple):
            set_tile(rows[n_codes_guessed], current_guess[0], current_guess[1])
        if confirm_guess:
            if current_guess is None or not all([isinstance(k, Color) for k in rows[n_codes_guessed]]):
                show_hint_confirmation_not_possible = True
            else:
                pins[n_codes_guessed] = calc_pins(rows[n_codes_guessed], solution_code)
                if rows[n_codes_guessed] == solution_code:
                    print_all(rows, pins, solution_code, marked_row=None)
                    fancyprint('Du hast den Code geknackt und das Spiel gewonnen!\n', fg=GREEN, styles=BOLD)
                    return
                n_codes_guessed += 1
                if n_codes_guessed == LENGTH:
                    print_all(rows, pins, solution_code, marked_row=None)
                    fancyprint('Du hast es leider nicht geschafft, den Code zu knacken, und das Spiel verloren.\n', fg=RED)
                    return
                current_guess = None
            confirm_guess = False


def set_tile(row: list[Color | None], index: int, value: Color | int) -> None:
    if isinstance(value, Color):
        row[index] = value
    elif value == DELETE:
        row[index] = None
            
            
def calc_pins(code_guess: list[Color], solution_code: list[Color]) -> list[int]:
    correct_position: list[int] = []
    for (i, (g, s)) in enumerate(zip(code_guess, solution_code)):
        if g is s:
            correct_position.append(i)
    n_correct_position = len(correct_position)
    code_guess = [g for (i, g) in enumerate(code_guess) if i not in correct_position]
    solution_code = [s for (i, s) in enumerate(solution_code) if i not in correct_position]
    n_correct_color = 0
    for g in code_guess:
        if g in solution_code:
            n_correct_color += 1
            solution_code.remove(g)
    pins = ([CORRECT_POSITION] * n_correct_position) + ([CORRECT_COLOR] * n_correct_color)
    return pins


def print_all(rows: list[list[Color | None]], pins: list[list[int] | None], solution_code: list[Color] | None, marked_row: int | None) -> None:
    clear()
    print_title_and_instructions()
    line_breaks(1)
    print_board(rows, pins, solution_code, marked_row, shift=15)
    line_breaks(1)


def print_board(rows: list[list[Color | None]], pins: list[list[int] | None], solution_code: list[Color] | None,
                marked_row: int | None, shift: int = 0) -> None:
    print_frame_part(' ┏' + '━━━━━━━' * 4 + '┓        ', shift)
    if solution_code is None:
        for _ in range(2):
            print_frame_part(' ┃' + '       ' * 4 + '┃        ', shift)
    else:
        print_row(solution_code, None, shift, marked=False, complete_row=True)
    print_frame_part(' ┣' + '━━━━━━━' * 4 + '╋━━━━━━┓ ', shift, marked=(marked_row == LENGTH - 1))
    for i in range(LENGTH - 1, 0, -1):
        print_row(rows[i], pins[i], shift, marked=(i == marked_row))
        print_frame_part(' ┣' + '━━━━━━━' * 4 + '╋━━━━━━┫ ', shift, marked=(i == marked_row or i - 1 == marked_row))
    print_row(rows[0], pins[0], shift, marked=(marked_row == 0))
    print_frame_part(' ┗' + '━━━━━━━' * 4 + '┻━━━━━━┛ ', shift, marked=(marked_row == 0))

    
def print_row(row: list[Color | None], pins: list[int] | None, shift: int, marked: bool, complete_row: bool = False) -> None:
    print_row_half(row, None if pins is None else pins[:2], shift, marked, ' ▄███▄ ', '  ▄▄▄  ', ' ▀ ', end='')
    if complete_row:
        fancyprint('       ', bg=Colors.BOARD_BG, end='')
    line_breaks(1)
    print_row_half(row, None if pins is None else pins[2:], shift, marked, ' ▀███▀ ', '  ▀▀▀  ', ' ▄ ', end='')
    if complete_row:
        fancyprint('       ', bg=Colors.BOARD_BG, end='')
    line_breaks(1)


def print_row_half(row: list[Color | None], pins: list[int] | None, shift: int, marked: bool, ball_half: str, hole_half: str, pinstr: str, end: str) -> None:
    if marked:
        holes = Colors.BOARD_HOLES_MARKED
        bg = Colors.BOARD_BG_MARKED
    else:
        holes = Colors.BOARD_HOLES
        bg = Colors.BOARD_BG
    move_cursor_right(shift)
    print_frame_part(' ┃', marked=marked, end='')
    for k in row:
        if k is None:
            fancyprint(hole_half, fg=holes, bg=bg, end='')
        else:
            fancyprint(ball_half, fg=k.color, bg=bg, end='')
    if pins is not None:
        print_frame_part('┃', marked=marked, end='')
        for p in pins:
            fancyprint(pinstr, fg=(RED if p == CORRECT_POSITION else WHITE), bg=bg, end='')
        for _ in range(2 - len(pins)):
            fancyprint(pinstr, fg=holes, bg=bg, end='')
    print_frame_part('┃ ', marked=marked, end=end)


# Diese Methode gibt einen Teil des Rahmens aus
def print_frame_part(frame_part: str, shift: int = 0, marked: bool = False, end: str | None = '\n') -> None:
    if marked:
        fg = Colors.BOARD_LINES_MARKED
        bg = Colors.BOARD_BG_MARKED
    else:
        fg = Colors.BOARD_LINES
        bg = Colors.BOARD_BG
    fancyprint(frame_part, fg=fg, bg=bg, styles=DIM, shift=shift, end=end)


# Diese Methode gibt den Titel und eine Anweisung aus
def print_title_and_instructions() -> None:
    fancyprint(TITLE, fg=CYAN, styles=BOLD) # Überschrift in Cyan, fett, doppelt unterstrichen
    line_breaks(1)
    fancyprintf('<b>Gib jederzeit »<f,c>/Hilfe<!a,b>« ein, um die Spielanleitung ausgegeben zu bekommen.')
    # modifizierte Anweisung, wie das Programm beendet wird
    fancyprintf('<b>Gib jederzeit »<f,r>/Ende<!a,b>« ein, um das Programm zu beenden.')


def print_game_instructions() -> None:
    lb = (N_GAME_INSTRUCTIONS_LINES - N_BOARD_LINES) // 2
    clear()
    line_breaks(lb)
    print_board(EXAMPLE_BOARD_ROWS, EXAMPLE_BOARD_PINS, solution_code=None, marked_row=4, shift=84)
    move_cursor_up(N_BOARD_LINES + lb)
    fancyprintf(GAME_INSTRUCTIONS)
    line_breaks(1)
    await_enter('<m>Zum Fortfahren die Entertaste drücken. ')


def input_guess() -> None:
    fancyprintf('<b>Du hast die folgenden Farben zur Auswahl: %s<b> und %s<b>.' % ('<b>, '.join(k.highlighted_name(True) for k in COLORS[:-1]),
                COLORS[-1].highlighted_name(True)))
    line_breaks(1)
    fancyinput('<b>Gib deinen Tipp auf den Code ein. <!a,f>\\>\\> ', '<c>', commands=COMMANDS,
               exit_codes=EXIT_CODES, special_inputs_ignore_case=True, call_before_exit=print_program_ended,
               validate_func=validate_guess, valid_input_marking_format_tag=VALID_INPUT_MARKING_FORMAT_TAG,
               invalid_input_marking_format_tag=INVALID_INPUT_MARKING_FORMAT_TAG,
               command_marking_format_tag=COMMAND_MARKING_FORMAT_TAG,
               exit_code_marking_format_tag=EXIT_CODE_MARKING_FORMAT_TAG, marking_extras=MarkingExtra.UPPER_CASE)


def validate_guess(guess: str) -> str | None:
    global current_guess, confirm_guess
    guess = re.sub(r'[ ,]', '', guess.upper())
    confirm_guess_local = False
    if len(guess) == 0:
        return INVALID_INPUT_MSG
    if guess == '.':
        confirm_guess = True
        return None
    if guess[-1] == '.':
        confirm_guess_local = True
        guess = guess[:-1]
    if len(guess) == 2 and guess[0] in '1234' and guess[1] in AVAILABLE_INPUT_LETTERS:
        current_guess = (int(guess[0]) - 1, LETTER_COLOR_MAPPING[guess[1]])
        confirm_guess = confirm_guess_local
        return None
    if len(guess) != 4:
        return INVALID_INPUT_MSG
    for c in guess:
        if c not in AVAILABLE_INPUT_LETTERS:
            return INVALID_INPUT_MSG
    current_guess = [LETTER_COLOR_MAPPING[c] for c in guess]
    confirm_guess = confirm_guess_local
    return None

    
# In dieser Funktion wird abgefragt, ob der Spieler noch einmal spielen möchte, und das Ergebnis wird zurückgegeben
def input_play_again() -> bool:
    while True:
        try:
            return fancyinput_yn('<m>Möchtest du noch einmal spielen? (<f,g>J<!f,m>/<f,r>n<!f,m>) <!a,f>\\>\\> ', '<b>', 'J', 'n',
                                 ignore_case=True, commands=COMMANDS, exit_codes=EXIT_CODES, special_inputs_ignore_case=True,
                                 invalid_input_msg_format='<i,r>Ungültige Eingabe. Gib »J« oder »n« ein.',
                                 call_before_exit=print_program_ended, y_marking_format_tag=Y_MARKING_FORMAT,
                                 n_marking_format_tag=N_MARKING_FORMAT, invalid_input_marking_format_tag=INVALID_INPUT_MARKING_FORMAT_TAG,
                                 command_marking_format_tag=COMMAND_MARKING_FORMAT_TAG,
                                 exit_code_marking_format_tag=EXIT_CODE_MARKING_FORMAT_TAG)
        except CommandInputException:
            print_game_instructions()
            line_breaks(1)


def random_code() -> list[Color]:
    return [random.choice(COLORS) for _ in range(4)]


def clear() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


# Diese Funktion gibt aus, dass das Programm beendet wurde
def print_program_ended() -> None:
    line_breaks(1)
    fancyprint('Das Programm wurde beendet.', fg=MAGENTA)


if __name__ == '__main__':
    main()