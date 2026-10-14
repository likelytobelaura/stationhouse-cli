"""The welcome train: the first time someone runs `stationhouse` in a real terminal, a little train
rolls across it once (`stationhouse train` plays it again). Never for scripts or coding agents: not
when output isn't a terminal, not with --json, not on a dumb or very narrow terminal, and never with
STATIONHOUSE_NO_TRAIN set.
"""
import os
import shutil
import sys
import time

from .store import load_settings, save_settings

# Drawn for Station House. Two smoke frames, so the engine puffs as it goes.
SMOKE = (
    ["      o  O   o", "    o           ", "   o            "],
    ["        O  o  O", "     O           ", "    o            "],
)
TRAIN = [
    "   _||_____     ______________   ______________ ",
    "  |  ___   |   |   STATION    | |    HOUSE     |",
    "  | |_|_|  |===|  []  []  []  |=|  []  []  []  |",
    "  |________|   |______________| |______________|",
    " __(O)--(O)______(O)------(O)_____(O)------(O)__",
]
WIDTH_NEEDED = 40
STEP = 2
FRAME_SECONDS = 0.025


def frames(width: int) -> list[list[str]]:
    """Every frame of the train crossing a terminal `width` wide, right to left: lists of lines, each
    at most width - 1 characters (the last column is left alone so lines never wrap)."""
    usable = width - 1
    art_width = max(len(line) for line in TRAIN + SMOKE[0] + SMOKE[1])
    out = []
    # Step across, then one last frame exactly off the left edge (the steps may not land on it).
    for i, x in enumerate([*range(usable, -art_width, -STEP), -art_width]):
        art = SMOKE[i // 3 % 2] + TRAIN
        lines = []
        for line in art:
            line = line.ljust(art_width)
            shown = (" " * x + line) if x >= 0 else line[-x:]
            lines.append(shown[:usable].rstrip())
        out.append(lines)
    return out


def play(stream=None, sleep=time.sleep, width: int | None = None) -> None:
    """Draws the frames in place, then clears them. The cursor always comes back, even on Ctrl-C."""
    stream = stream or sys.stdout
    width = width or shutil.get_terminal_size(fallback=(80, 24)).columns
    if width < WIDTH_NEEDED:
        return
    height = len(SMOKE[0]) + len(TRAIN)
    write = stream.write
    write("\033[?25l")  # hide the cursor while it runs
    try:
        write("\n" * height)
        for lines in frames(width):
            write(f"\033[{height}F")  # back up to the top of the drawing
            for line in lines:
                write(f"{line}\033[K\n")
            stream.flush()
            sleep(FRAME_SECONDS)
        write(f"\033[{height}F" + "\033[K\n" * height + f"\033[{height}F")  # leave no trace
    finally:
        write("\033[?25h")
        stream.flush()


def should_welcome(json_output: bool) -> bool:
    """The first run in a real terminal, by a person."""
    if json_output or os.environ.get("STATIONHOUSE_NO_TRAIN") or os.environ.get("TERM") == "dumb":
        return False
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        return False
    return not load_settings().get("welcomed")


def welcome(json_output: bool) -> None:
    """Plays the train the first time only, then remembers that it did. Never gets in the way: any
    problem drawing it is ignored."""
    if not should_welcome(json_output):
        return
    settings = load_settings()
    settings["welcomed"] = True
    try:
        save_settings(settings)
    except OSError:
        return  # couldn't remember it, so don't play it every time
    try:
        play()
    except KeyboardInterrupt:
        sys.stdout.write("\n")  # Ctrl-C skips the train, not the command you ran
    except (OSError, ValueError):
        return
    sys.stdout.write("Welcome to Station House.\n\n")
