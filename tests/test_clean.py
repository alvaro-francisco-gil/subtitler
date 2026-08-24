from pathlib import Path

import pytest

from subtitler import clean

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("I. Una confesión, para empezar", True),
        ("II. El nombre", True),
        ("VII. Cierre: Matamala libre", True),
        ("Vecinas y vecinos de Matamala.", False),
        ("I am not a heading", False),
        ("", False),
    ],
)
def test_is_heading(line, expected):
    assert clean.is_heading(line) is expected


def test_normalise_typography():
    raw = 'contra —y cito— «la brutal agresión»… "hola"'
    assert clean.normalise(raw) == 'contra -y cito- "la brutal agresión"... "hola"'


def test_headings_and_title_are_dropped():
    result = clean.clean_transcript((FIXTURES / "transcript_sample.txt").read_text())

    assert "Pregón de las fiestas" not in result.text
    assert "Una confesión" not in result.text
    assert "El nombre" not in result.text
    assert "Vecinas y vecinos de Matamala." in result.text
    assert "¡Viva Matamala!" in result.text


def test_sentence_punctuation_is_preserved():
    result = clean.clean_transcript("Hola mundo.\n\n¿Qué tal?\n")
    assert result.text == "Hola mundo. ¿Qué tal?"


def test_whitespace_collapses_and_lines_join():
    result = clean.clean_transcript("Empieza aqui.\n\nuno   dos\n\n\ntres\n")
    assert result.text == "Empieza aqui. uno dos tres"


def test_positions_point_back_at_the_original_file():
    raw = "Titulo sin punto\n\nHola mundo.\n\nI. Seccion\n\nAdios amigo.\n"
    result = clean.clean_transcript(raw)

    assert result.text == "Hola mundo. Adios amigo."
    assert len(result.positions) == len(result.text.split())

    # "Hola" is on line 3 at column 1; "Adios" is on line 7 at column 1.
    assert result.positions[0] == clean.SourcePos(line=3, column=1)
    assert result.positions[1] == clean.SourcePos(line=3, column=6)
    assert result.positions[2] == clean.SourcePos(line=7, column=1)


def test_a_title_that_ends_in_punctuation_is_kept():
    result = clean.clean_transcript("Buenas noches.\n\nHola.\n")
    assert result.text == "Buenas noches. Hola."
