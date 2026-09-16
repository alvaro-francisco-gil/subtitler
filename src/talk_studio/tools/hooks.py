"""Hook titles: the line over a Short's first seconds that has to stop the scroll.

The plain title card (`fade`) comes from the caption generator. The styles here
are louder: each lays the hook out word by word with the measurer libass will
match, then animates the words in. libass draws no colour emoji, so an emoji is
laid out as one more word and handed back as a spot for ffmpeg to overlay a PNG.
"""

from __future__ import annotations

import dataclasses
import urllib.request
from pathlib import Path

from .. import cache
from ..captions import measure
from ..captions.ass import ass_time, escape
from ..captions.style import ass_colour

FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"
EMOJI_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/png/512/{name}.png"
MAX_WIDTH = 0.88  # widest a hook line may be, as a fraction of frame width
TOP_MARGIN = 0.06  # the block never starts above this, as a fraction of height


@dataclasses.dataclass(frozen=True)
class Look:
    family: str
    font: str
    em: float            # rendered em height in pixels at hook_size 1
    caps: bool
    fill: str
    accent: str          # the last word
    outline: str
    outline_width: float
    shadow: float
    line_spacing: float
    boxed: bool = False  # an opaque box behind each line, in `outline` colour


LOOKS = {
    # Anton, huge, each word slamming down from twice its size.
    "slam": Look("Anton", "Anton-Regular.ttf", 140, True, "#FFFFFF", "#F2C14E", "#000000", 8, 5, 1.08),
    # Dark words on tilted white and yellow strips, popping in line by line.
    "sticker": Look("Montserrat ExtraBold", "Montserrat-ExtraBold.ttf", 96, False, "#111111", "#111111", "#FFFFFF", 22, 0, 1.55, boxed=True),
    # Bangers comic lettering, words bouncing up with a wobble, then breathing.
    "comic": Look("Bangers", "Bangers-Regular.ttf", 150, True, "#FFE14D", "#FFFFFF", "#000000", 10, 8, 1.05),
}
STYLES = ("fade",) + tuple(LOOKS)


@dataclasses.dataclass(frozen=True)
class EmojiSpot:
    text: str
    x: int       # centre
    y: int
    size: int
    start: float
    end: float


@dataclasses.dataclass(frozen=True)
class Hook:
    styles: list[str]
    events: list[str]
    emoji: EmojiSpot | None


@dataclasses.dataclass(frozen=True)
class _Token:
    text: str      # empty for the emoji
    width: float
    line: int
    x: float = 0.0  # centre


def _layout(tokens: list[_Token], usable: float, gap: float) -> list[list[_Token]]:
    lines: list[list[_Token]] = [[]]
    for token in tokens:
        current = lines[-1]
        span = sum(t.width for t in current) + gap * len(current) + token.width
        if current and span > usable:
            lines.append([])
        lines[-1].append(token)
    return lines


def build(text: str, style: str, emoji: str, size: float, at: float, hold: float, centre_y: float,
          width: int, height: int, _shrinks: int = 0) -> Hook:
    look = LOOKS[style]
    em = look.em * size
    font_size = int(round(em / measure.libass_size_ratio(FONTS / look.font)))
    measurer = measure.text_measurer(FONTS / look.font, font_size)
    words = (text.upper() if look.caps else text).split()
    gap = measurer(" ") * (2.2 if look.boxed else 1.6 if style == "comic" else 1.0)
    tokens = [_Token(w, measurer(w), 0) for w in words]
    if emoji:
        tokens.append(_Token("", em * 1.2, 0))

    usable = width * MAX_WIDTH
    widest = max(t.width for t in tokens)
    if widest > usable:  # one word wider than the frame: shrink everything
        shrink = usable / widest
        return build(text, style, emoji, size * shrink, at, hold, centre_y, width, height, _shrinks)

    lines = _layout(tokens, usable, gap)
    if emoji and len(lines[-1]) == 1 and len(lines) > 1 and _shrinks < 4:  # keep the emoji beside a word
        return build(text, style, emoji, size * 0.93, at, hold, centre_y, width, height, _shrinks + 1)
    line_height = em * look.line_spacing
    top = max(height * centre_y - line_height * len(lines) / 2, height * TOP_MARGIN)
    placed: list[tuple[_Token, float]] = []
    for index, line in enumerate(lines):
        span = sum(t.width for t in line) + gap * (len(line) - 1)
        x = (width - span) / 2
        y = top + line_height * (index + 0.5)
        for token in line:
            placed.append((dataclasses.replace(token, line=index, x=x + token.width / 2), y))
            x += token.width + gap

    name = f"H{style}"
    border_style = 3 if look.boxed else 1
    styles = [
        f"Style: {name},{look.family},{font_size},{ass_colour(look.fill)},{ass_colour(look.fill)},"
        f"{ass_colour(look.outline)},{ass_colour('#000000')},0,0,0,0,100,100,0,0,{border_style},"
        f"{look.outline_width:g},{look.shadow:g},5,0,0,0,1"
    ]
    if look.boxed:  # the second line's strip is yellow
        styles.append(styles[0].replace(f"Style: {name},", f"Style: {name}2,").replace(
            f",{ass_colour('#FFFFFF')},{ass_colour('#000000')},", f",{ass_colour('#F2C14E')},{ass_colour('#000000')},"))

    end = at + hold
    events = []
    if look.boxed:
        events = _sticker([p for p in placed if p[0].text], lines, name, at, end, top, line_height)
        landed = at + (len(lines) - 1) * 0.18 + 0.2
    else:
        last_word = max(i for i, (t, _) in enumerate(placed) if t.text)
        for index, (token, y) in enumerate(placed):
            start = at + index * (0.13 if style == "slam" else 0.16)
            if not token.text:
                continue
            colour = f"\\1c{ass_colour(look.accent)}" if index == last_word else ""
            animate = _slam if style == "slam" else _comic
            tags = animate(int(token.x), int(y), index, end - start, colour)
            events.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},{name},,0,0,0,,{{{tags}}}{escape(token.text)}")
        landed = at + last_word * (0.13 if style == "slam" else 0.16) + 0.2
    emoji_spot = None
    if emoji:
        spot, y = placed[-1]
        x = spot.x + (look.outline_width if look.boxed else 0)
        emoji_spot = EmojiSpot(emoji, int(x), int(y), int(em * 1.1), landed, end)
    return Hook(styles, events, emoji_spot)


def _slam(x: int, y: int, index: int, duration: float, colour: str) -> str:
    total = int(duration * 1000)
    return (
        f"\\an5\\pos({x},{y}){colour}\\alpha&HFF&\\fscx230\\fscy230"
        f"\\t(0,90,\\alpha&H00&\\fscx92\\fscy92)\\t(90,170,\\fscx100\\fscy100)"
        f"\\t({total - 160},{total},\\alpha&HFF&\\fscx60\\fscy60)"
    )


def _comic(x: int, y: int, index: int, duration: float, colour: str) -> str:
    total = int(duration * 1000)
    tilt = 10 if index % 2 else -10
    return (
        f"\\an5\\move({x},{y + 70},{x},{y},0,180){colour}\\alpha&HFF&\\frz{tilt}\\fscx50\\fscy50"
        f"\\t(0,180,\\alpha&H00&\\frz{-tilt // 3}\\fscx112\\fscy112)\\t(180,300,\\frz0\\fscx100\\fscy100)"
        f"\\t(300,{total - 200},\\fscx108\\fscy108)\\t({total - 200},{total},\\alpha&HFF&)"
    )


def _sticker(placed, lines, name, at, end, top, line_height):
    events = []
    centre_x = int(sum(t.x for t, _ in placed) / len(placed))
    centre_y = int(top + line_height * len(lines) / 2)
    for index in range(len(lines)):
        start = at + index * 0.18
        tokens = [(t, y) for t, y in placed if t.line == index]
        left = tokens[0][0].x - tokens[0][0].width / 2
        right = tokens[-1][0].x + tokens[-1][0].width / 2
        x, y = int((left + right) / 2), int(tokens[0][1])
        duration = int((end - start) * 1000)
        tags = (
            f"\\an5\\pos({x},{y})\\org({centre_x},{centre_y})\\frz-12\\fscx0\\fscy0"
            f"\\t(0,140,\\frz-1\\fscx112\\fscy112)\\t(140,240,\\frz-3\\fscx100\\fscy100)"
            f"\\t({duration - 180},{duration},\\alpha&HFF&)"
        )
        style = name + ("2" if index % 2 else "")
        text = " ".join(t.text for t, _ in tokens)
        events.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{{{tags}}}{escape(text)}")
    return events


# emoji


def emoji_name(text: str) -> str:
    return "emoji_u" + "_".join(f"{ord(ch):x}" for ch in text if ord(ch) != 0xFE0F)


def emoji_image(text: str) -> Path:
    """The Noto emoji PNG for `text`, fetched once into the cache."""
    name = emoji_name(text)
    path = cache.root() / "emoji" / f"{name}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        urllib.request.urlretrieve(EMOJI_URL.format(name=name), partial)
        partial.replace(path)
    return path


def emoji_overlay(spot: EmojiSpot, image_input: int, below: str, out: str) -> str:
    """Filter graph that pops the emoji in over `below`, bobbing gently until the hook ends."""
    half = spot.size // 2
    rise = f"60*max(0\\,1-(t-{spot.start:.3f})/0.18)"
    bob = f"10*sin(7*(t-{spot.start:.3f}))"
    return (
        f"[{image_input}:v]scale={spot.size}:{spot.size},format=rgba,"
        f"fade=in:st={spot.start:.3f}:d=0.15:alpha=1,fade=out:st={spot.end - 0.2:.3f}:d=0.2:alpha=1[emoji];"
        f"[{below}][emoji]overlay=x={spot.x - half}:y='{spot.y - half}+{rise}+{bob}'"
        f":enable='between(t,{spot.start:.3f},{spot.end:.3f})':shortest=1[{out}]"
    )
