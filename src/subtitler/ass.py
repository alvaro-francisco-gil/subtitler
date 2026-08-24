"""Generating the ASS subtitle document.

Each word is positioned individually with `\\pos` so the highlight can change
colour on one word without relayouting the rest of the cue. That means this
module has to do its own centring, which is why it needs a text measurer.

The measurer is injected rather than imported so this module stays pure and
its layout maths can be tested with a predictable stub.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .models import Cue
from .style import Style, ass_colour

HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: K,{family},{size},{fill},{fill},{outline},&H00000000,0,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def ass_time(seconds: float) -> str:
    """Format seconds as the ASS `H:MM:SS.cc` timecode."""
    centiseconds = int(round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def escape(text: str) -> str:
    """Escape the characters ASS treats as override-block syntax."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _pop_tags(style: Style) -> str:
    """Scale overshoot applied when a cue first appears."""
    if style.pop_ms <= 0 or style.pop_scale == 1.0:
        return ""
    start_scale = int(round(100 / style.pop_scale))
    peak_scale = int(round(100 * style.pop_scale))
    half = style.pop_ms // 2
    return (
        f"\\fscx{start_scale}\\fscy{start_scale}"
        f"\\t(0,{half},\\fscx{peak_scale}\\fscy{peak_scale})"
        f"\\t({half},{style.pop_ms},\\fscx100\\fscy100)"
    )


def _layout(cue: Cue, style: Style, width: int, measure) -> list[float]:
    """Horizontal centre for each word, centring the cue as a group."""
    texts = [_render_text(word.text, style) for word in cue.words]
    widths = [measure(text) for text in texts]
    space = measure(" ")

    total = sum(widths) + space * (len(widths) - 1)
    cursor = (width - total) / 2

    centres = []
    for word_width in widths:
        centres.append(cursor + word_width / 2)
        cursor += word_width + space
    return centres


def _render_text(text: str, style: Style) -> str:
    return text.upper() if style.all_caps else text


def build_ass(
    cues: Sequence[Cue],
    style: Style,
    width: int,
    height: int,
    measure: Callable[[str], float],
) -> str:
    header = HEADER_TEMPLATE.format(
        width=width,
        height=height,
        family=style.font_family,
        size=style.font_size,
        fill=ass_colour(style.fill),
        outline=ass_colour(style.outline),
        outline_width=_number(style.outline_width),
        shadow=_number(style.shadow_depth),
    )

    fill = ass_colour(style.fill)
    highlight = ass_colour(style.highlight)
    y = int(round(height * style.position))
    pop = _pop_tags(style)

    events: list[tuple[float, str]] = []

    for cue in sorted(cues, key=lambda c: c.start):
        centres = _layout(cue, style, width, measure)

        for word, centre_x in zip(cue.words, centres):
            text = escape(_render_text(word.text, style))
            x = int(round(centre_x))

            phases = [
                (cue.start, word.start, fill),
                (word.start, word.end, highlight),
                (word.end, cue.end, fill),
            ]

            for start, end, colour in phases:
                if end - start <= 0.001:
                    continue
                tags = f"\\an5\\pos({x},{y})\\c{colour}&"
                if abs(start - cue.start) < 0.001:
                    tags += pop
                events.append((
                    start,
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},K,,0,0,0,,{{{tags}}}{text}",
                ))

    ordered = [line for _, line in sorted(events, key=lambda item: item[0])]
    return "\n".join([header, *ordered]) + "\n"


def _number(value: float) -> str:
    return f"{value:g}"
