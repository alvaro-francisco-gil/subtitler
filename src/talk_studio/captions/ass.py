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
Style: T,{family},{title_size},{fill},{fill},{outline},&H00000000,0,0,0,0,100,100,0,0,1,{title_outline},{shadow},5,0,0,0,1

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


def _fit_scale(texts: list[str], style: Style, width: int, measure) -> float:
    """How much a cue must shrink to fit the frame.

    Each word is positioned individually with \\pos and WrapStyle 2 disables
    wrapping, so an over-wide cue cannot break onto a second line — it simply
    runs off both edges. Scaling the whole cue keeps its grouping and timing
    intact while guaranteeing it fits.
    """
    gap = measure(" ") * style.word_spacing
    total = sum(measure(text) for text in texts) + gap * (len(texts) - 1)
    usable = width * style.max_width
    if total <= usable or total <= 0:
        return 1.0
    return usable / total


def _layout(cue: Cue, style: Style, width: int, measure, scale: float) -> list[float]:
    """Horizontal centre for each word, centring the cue as a group.

    The gap between words is the font's own space advance scaled by
    `style.word_spacing`. Montserrat's space is about 0.29 of the font size,
    which reads loose at heavy weights and short words, so the multiplier
    exists to tighten it without changing the font. `scale` shrinks an
    over-wide cue (see `_fit_scale`) so its centring maths matches the size it
    will actually render at.
    """
    texts = [_render_text(word.text, style) for word in cue.words]
    widths = [measure(text) * scale for text in texts]
    gap = measure(" ") * style.word_spacing * scale

    total = sum(widths) + gap * (len(widths) - 1)
    cursor = (width - total) / 2

    centres = []
    for word_width in widths:
        centres.append(cursor + word_width / 2)
        cursor += word_width + gap
    return centres


def _scale_tags(style: Style, scale: float) -> tuple[str, str]:
    """Return (tags for every event, extra tags for events starting the cue).

    The base scale is what a shrunken cue must hold for its whole life; the
    pop transform rides on top of it and must settle back to the base, not
    to 100%, or a shrunken cue would snap to full width mid-animation.
    """
    base = 100.0 * scale
    base_tags = "" if scale == 1.0 else f"\\fscx{base:.0f}\\fscy{base:.0f}"

    if style.pop_ms <= 0 or style.pop_scale == 1.0:
        return base_tags, ""

    start = base / style.pop_scale
    peak = base * style.pop_scale
    half = style.pop_ms // 2
    pop = (
        f"\\fscx{start:.0f}\\fscy{start:.0f}"
        f"\\t(0,{half},\\fscx{peak:.0f}\\fscy{peak:.0f})"
        f"\\t({half},{style.pop_ms},\\fscx{base:.0f}\\fscy{base:.0f})"
    )
    return base_tags, pop


def wrap_title(text: str, style: Style, width: int, measure) -> list[str]:
    """Break the title into lines that fit the frame.

    Greedy fill: a title is a handful of words, and the alternative — balancing
    the lines — reads worse when the last line ends up a lone short word.
    """
    usable = width * style.max_width
    lines: list[str] = []
    current: list[str] = []

    for word in text.split():
        candidate = current + [word]
        if current and measure(" ".join(candidate)) > usable:
            lines.append(" ".join(current))
            current = [word]
        else:
            current = candidate

    if current:
        lines.append(" ".join(current))
    return lines


def title_events(
    text: str,
    at: float,
    style: Style,
    width: int,
    height: int,
    em: float,
    measure,
) -> list[tuple[float, str]]:
    """A title card: each line fades in while rising slightly, one after another.

    `\move` and `\pos` are mutually exclusive, so the rise *is* the
    positioning — the line travels from its offset start to its resting place
    over the fade-in and stays there. `em` is the rendered em height, which is
    what the line spacing has to be built from; see `measure.rendered_em`.
    """
    lines = wrap_title(text, style, width, measure)
    if not lines:
        return []

    line_height = em * style.title_line_spacing
    block_top = height * style.title_position - (line_height * len(lines)) / 2
    rise = height * style.title_rise
    centre_x = width // 2

    events = []
    for index, line in enumerate(lines):
        resting_y = int(round(block_top + line_height * (index + 0.5)))
        start = at + index * style.title_stagger_ms / 1000
        end = at + style.title_hold
        if end <= start:
            continue
        tags = (
            f"\\an5"
            f"\\move({centre_x},{resting_y + int(round(rise))},{centre_x},{resting_y}"
            f",0,{style.title_fade_ms})"
            f"\\fad({style.title_fade_ms},{style.title_fade_ms})"
        )
        events.append((
            start,
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},T,,0,0,0,,{{{tags}}}{escape(line)}",
        ))
    return events


def _render_text(text: str, style: Style) -> str:
    return text.upper() if style.all_caps else text


def build_ass(
    cues: Sequence[Cue],
    style: Style,
    width: int,
    height: int,
    measure: Callable[[str], float],
    *,
    title: str | None = None,
    title_at: float = 0.0,
    title_em: float = 0.0,
    title_measure: Callable[[str], float] | None = None,
) -> str:
    header = HEADER_TEMPLATE.format(
        width=width,
        height=height,
        family=style.font_family,
        size=style.font_size,
        title_size=style.title_size,
        fill=ass_colour(style.fill),
        outline=ass_colour(style.outline),
        outline_width=_number(style.outline_width),
        title_outline=_number(style.outline_width * style.title_size / style.font_size),
        shadow=_number(style.shadow_depth),
    )

    fill = ass_colour(style.fill)
    highlight = ass_colour(style.highlight)
    y = int(round(height * style.position))

    events: list[tuple[float, str]] = []

    if title:
        events += title_events(
            title, title_at, style, width, height,
            title_em, title_measure or measure,
        )

    for cue in sorted(cues, key=lambda c: c.start):
        texts = [_render_text(word.text, style) for word in cue.words]
        scale = _fit_scale(texts, style, width, measure)
        centres = _layout(cue, style, width, measure, scale)
        base_tags, pop = _scale_tags(style, scale)

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
                if abs(start - cue.start) < 0.001 and pop:
                    tags += pop
                else:
                    tags += base_tags
                events.append((
                    start,
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},K,,0,0,0,,{{{tags}}}{text}",
                ))

    ordered = [line for _, line in sorted(events, key=lambda item: item[0])]
    return "\n".join([header, *ordered]) + "\n"


def _number(value: float) -> str:
    return f"{value:g}"
