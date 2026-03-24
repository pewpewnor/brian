#!/usr/bin/env python3

import getpass
import os
import platform
import site
import sys

if os.name != "nt":
    if os.geteuid() != 0:
        try:
            os.execvp("sudo", ["sudo", "python3"] + sys.argv)
        except Exception as e:
            print(f"Failed to elevate privileges: {e}")
            sys.exit(1)

real_user = os.getenv("SUDO_USER") or getpass.getuser()


def get_user_site_path(real_user):
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    system = platform.system()
    if system == "Linux":
        return f"/home/{real_user}/.local/lib/python{py_version}/site-packages"
    elif system == "Darwin":
        return (
            f"/Users/{real_user}/Library/Python/{py_version}/lib/python/site-packages"
        )
    elif system == "Windows":
        return os.path.expandvars(
            rf"C:\Users\{real_user}\AppData\Roaming\Python\Python{sys.version_info.major}{sys.version_info.minor}\site-packages"
        )
    return None


user_site = get_user_site_path(real_user)
if user_site and os.path.isdir(user_site):
    site.addsitedir(user_site)

if platform.system() == "Linux":
    user_uid = os.popen(f"id -u {real_user}").read().strip()
    xdg_runtime_dir = f"/run/user/{user_uid}"
    os.environ["XDG_RUNTIME_DIR"] = xdg_runtime_dir
    os.environ["PULSE_SERVER"] = f"unix:{xdg_runtime_dir}/pulse/native"

import math
import re
import threading
import time
import warnings
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import click
import pytermgui as ptg
import pyttsx3


def parse_text(text: str) -> List[List[str]]:
    common_abbreviations = {
        "Mr.",
        "Mrs.",
        "Ms.",
        "Dr.",
        "St.",
        "Jr.",
        "Sr.",
        "Prof.",
        "Rev.",
        "Hon.",
        "Pres.",
        "Gov.",
        "Sen.",
        "Rep.",
        "Adm.",
        "Gen.",
        "Lt.",
        "Col.",
        "Maj.",
        "Capt.",
        "Sgt.",
        "Cpl.",
        "Mt.",
        "Ave.",
        "Blvd.",
        "Rd.",
        "Ln.",
        "e.g.",
        "i.e.",
        "etc.",
        "a.m.",
        "p.m.",
        "U.S.",
        "U.K.",
        "No.",
        "vs.",
        "Inc.",
        "Ltd.",
        "Co.",
        "Corp.",
        "Jan.",
        "Feb.",
        "Mar.",
        "Apr.",
        "Jun.",
        "Jul.",
        "Aug.",
        "Sep.",
        "Oct.",
        "Nov.",
        "Dec.",
    }

    raw_paragraphs = re.split(r"\n\s*\n+", text.strip())
    all_paragraphs = []
    sentence_splitter = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")

    for paragraph in raw_paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        tentative_sentences = sentence_splitter.split(paragraph)
        sentences = []
        buffer = ""

        for sentence in tentative_sentences:
            check = (buffer + " " + sentence).strip() if buffer else sentence
            last_word = check.split()[-1]
            if last_word in common_abbreviations:
                buffer += " " + sentence if buffer else sentence
                continue
            if buffer:
                sentences.append(buffer.strip())
                buffer = sentence
            else:
                buffer = sentence

        if buffer:
            sentences.append(buffer.strip())

        all_paragraphs.append(sentences)

    return all_paragraphs


class StatusLabel(Enum):
    READING = "[bold green]▶ Reading[/bold]"
    PAUSED = "[bold yellow]⏸ Paused[/bold]"
    STOPPING = "[bold red]⏹ Stopping...[/bold]"
    PAUSING = "[dim yellow]⏸ Pausing...[/dim]"


class Brian:
    def __init__(self):
        self.engine = pyttsx3.init()
        self.engine_lock = threading.Lock()

        self.paragraphs: List[List[str]] = []
        self.flat_sentences: List[Tuple[int, int, str]] = []
        self.flat_index_map: Dict[Tuple[int, int], int] = {}

        self.paragraph_index = 0
        self.sentence_index = 0
        self.view_start = 0
        self.view_height = 20
        self._term_height = 40
        self._term_width = 80
        self._visible_indices: Set[int] = set()

        self.reading_active = False
        self.stopped = False
        self.speech_wpm = 150
        self.current_thread: Optional[threading.Thread] = None

        self.content_label = ptg.Label("", parent_align=ptg.HorizontalAlignment.LEFT)
        self.status_text_label = ptg.Label()
        self.wpm_label = ptg.Label()
        self.progress_label = ptg.Label()

        self.update_status_label(StatusLabel.PAUSED)
        self.update_speech_wpm()
        self.engine.setProperty("rate", self.speech_wpm)

    def _build_flat_sentences(self):
        self.flat_sentences = []
        self.flat_index_map = {}
        for i, para in enumerate(self.paragraphs):
            for j, sent in enumerate(para):
                idx = len(self.flat_sentences)
                self.flat_sentences.append((i, j, sent))
                self.flat_index_map[(i, j)] = idx

    def _current_flat_index(self) -> int:
        return self.flat_index_map.get((self.paragraph_index, self.sentence_index), 0)

    def display(self, text: str):
        self.paragraphs = parse_text(text)
        self._build_flat_sentences()
        self._recalc_view_height()
        self.update_content_view()
        self.run_ui()

    def _recalc_view_height(self):
        term = ptg.Terminal()
        self._term_height = term.height
        self._term_width = term.width
        self.view_height = max(6, self._term_height - 10)

    def _content_width(self) -> int:
        return max(20, self._term_width - 8)

    def _sentence_lines(self, text: str) -> int:
        clean = re.sub(r"\[.*?\]", "", text)
        w = self._content_width()
        return max(1, math.ceil(len(clean) / w)) if clean else 1

    def update_status_label(self, status: StatusLabel):
        self.status_text_label.value = status.value

    def update_speech_wpm(self):
        self.wpm_label.value = f"[bold magenta]{self.speech_wpm} wpm[/bold]"

    def update_progress(self):
        total = len(self.flat_sentences)
        current = self._current_flat_index() + 1
        pct = int((current / total) * 100) if total else 0
        self.progress_label.value = f"[dim]{current}/{total} ({pct}%)[/dim]"

    def update_content_view(self):
        flat = self.flat_sentences
        total = len(flat)
        self._visible_indices = set()

        if not flat:
            self.content_label.value = "[dim]No content.[/dim]"
            return

        lines = []
        lines_used = 0
        prev_para = None

        for idx in range(self.view_start, total):
            para_i, sent_j, text = flat[idx]

            gap = 1 if (prev_para is not None and para_i != prev_para) else 0
            cost = self._sentence_lines(text) + gap

            if lines_used + cost > self.view_height and lines:
                break

            if gap:
                lines.append("")
                lines_used += 1

            is_past = (para_i, sent_j) < (self.paragraph_index, self.sentence_index)
            is_current = (para_i, sent_j) == (self.paragraph_index, self.sentence_index)

            if is_past:
                lines.append(f"[dim white]{text}[/dim white]")
            elif is_current:
                lines.append(f"[bold underline white]{text}[/bold /underline white]")
            else:
                lines.append(f"[white]{text}[/white]")

            lines_used += self._sentence_lines(text)
            self._visible_indices.add(idx)
            prev_para = para_i

        self.content_label.value = "\n".join(lines)
        self.update_progress()

    def _view_start_for_bottom(self, flat_idx: int) -> int:
        budget = self.view_height
        start = flat_idx
        for idx in range(flat_idx, -1, -1):
            para_i, _, text = self.flat_sentences[idx]
            cost = self._sentence_lines(text)
            if idx > 0:
                prev_para_i, _, _ = self.flat_sentences[idx - 1]
                if prev_para_i != para_i:
                    cost += 1
            if budget - cost < 0:
                break
            budget -= cost
            start = idx
        return start

    def ensure_visible(self):
        flat_idx = self._current_flat_index()
        if flat_idx < self.view_start:
            self.view_start = flat_idx
        elif flat_idx not in self._visible_indices:
            self.view_start = self._view_start_for_bottom(flat_idx)
            self.update_content_view()
            if flat_idx not in self._visible_indices:
                self.view_start = flat_idx

    def select_paragraph(self, delta: int):
        if self.reading_active:
            return
        self.paragraph_index = max(
            0, min(len(self.paragraphs) - 1, self.paragraph_index + delta)
        )
        self.sentence_index = 0
        self.ensure_visible()
        self.update_content_view()

    def select_sentence(self, delta: int):
        if self.reading_active:
            return
        flat_idx = max(
            0, min(len(self.flat_sentences) - 1, self._current_flat_index() + delta)
        )
        self.paragraph_index, self.sentence_index, _ = self.flat_sentences[flat_idx]
        self.ensure_visible()
        self.update_content_view()

    def jump_to_start(self):
        if self.reading_active:
            return
        self.paragraph_index = 0
        self.sentence_index = 0
        self.view_start = 0
        self.update_content_view()

    def jump_to_end(self):
        if self.reading_active:
            return
        if self.flat_sentences:
            self.paragraph_index, self.sentence_index, _ = self.flat_sentences[-1]
            self.ensure_visible()
            self.update_content_view()

    def pause_reading(self):
        if not self.reading_active:
            return
        self.update_status_label(StatusLabel.PAUSING)
        self.reading_active = False
        with self.engine_lock:
            self.engine.stop()
        self.update_status_label(StatusLabel.PAUSED)
        self.update_content_view()

    def unpause_reading(self):
        if self.reading_active:
            return
        self.reading_active = True
        self.update_status_label(StatusLabel.READING)
        if self.current_thread is None or not self.current_thread.is_alive():
            self.read_from_current_sentence()

    def toggle_pause_reading(self):
        if self.reading_active:
            self.pause_reading()
        else:
            self.unpause_reading()

    def slower(self):
        self.speech_wpm = max(10, self.speech_wpm - 10)
        self.engine.setProperty("rate", self.speech_wpm)
        self.update_speech_wpm()

    def faster(self):
        self.speech_wpm = min(400, self.speech_wpm + 10)
        self.engine.setProperty("rate", self.speech_wpm)
        self.update_speech_wpm()

    def read_from_current_sentence(self):
        def speaker_loop():
            finished = threading.Event()

            def on_end(name, completed):
                finished.set()

            self.engine.connect("finished-utterance", on_end)

            while not self.stopped:
                if not self.reading_active:
                    time.sleep(0.05)
                    continue

                i = self.paragraph_index
                j = self.sentence_index

                if i >= len(self.paragraphs):
                    self.reading_active = False
                    self.update_status_label(StatusLabel.PAUSED)
                    self.update_content_view()
                    return

                para = self.paragraphs[i]
                if j >= len(para):
                    self.reading_active = False
                    self.update_status_label(StatusLabel.PAUSED)
                    self.update_content_view()
                    return

                self.ensure_visible()
                self.update_content_view()

                finished.clear()
                with self.engine_lock:
                    self.engine.say(para[j])
                    self.engine.runAndWait()
                finished.wait(timeout=5.0)

                if (
                    not self.reading_active
                    or self.paragraph_index != i
                    or self.sentence_index != j
                ):
                    continue

                next_flat = self._current_flat_index() + 1
                if next_flat < len(self.flat_sentences):
                    self.paragraph_index, self.sentence_index, _ = self.flat_sentences[
                        next_flat
                    ]
                else:
                    self.reading_active = False
                    self.update_status_label(StatusLabel.PAUSED)
                    self.update_content_view()
                    return

        if self.current_thread is None or not self.current_thread.is_alive():
            self.current_thread = threading.Thread(target=speaker_loop, daemon=True)
            self.current_thread.start()

    def stop_gracefully(self, manager: ptg.WindowManager):
        self.stopped = True
        self.update_status_label(StatusLabel.STOPPING)
        with self.engine_lock:
            self.engine.stop()
        manager.stop()

    def quit_app(self, manager: ptg.WindowManager):
        manager.stop()
        sys.exit(0)

    def scroll_page(self, direction: int):
        step = max(1, len(self._visible_indices))
        max_start = max(0, len(self.flat_sentences) - 1)
        self.view_start = max(0, min(max_start, self.view_start + direction * step))
        self.update_content_view()

    def scroll_view(self, delta: int):
        max_start = max(0, len(self.flat_sentences) - self.view_height)
        self.view_start = max(0, min(max_start, self.view_start + delta))
        self.update_content_view()

    def run_ui(self):
        self._recalc_view_height()
        with ptg.WindowManager() as manager:
            manager.layout.add_slot("Body")

            title = ptg.Label("[bold]BRIAN[/bold]")
            instructions = ptg.Label(
                "[dim]↑↓ Para  ←→ Sent  j/k Scroll  PgUp/PgDn Page  g/G Jump start/end"
                "  Space/⏎ Toggle  ,/. Speed  p Pause  u Unpause  s Stop  q Quit[/dim]"
            )
            separator = ptg.Label("─" * (self._term_width - 10), style="dim")

            status_bar = ptg.Splitter(
                self.status_text_label,
                self.wpm_label,
                self.progress_label,
            )

            window = ptg.Window(
                instructions,
                title,
                status_bar,
                separator,
                self.content_label,
                ptg.Label(""),
                box="DOUBLE",
                is_resizable=True,
                vertical_align=ptg.VerticalAlignment.TOP,
                allow_scroll=True,
            )

            manager.add(window)

            manager.bind(ptg.keys.UP, lambda *_: self.select_paragraph(-1))
            manager.bind(ptg.keys.DOWN, lambda *_: self.select_paragraph(1))
            manager.bind(ptg.keys.LEFT, lambda *_: self.select_sentence(-1))
            manager.bind(ptg.keys.RIGHT, lambda *_: self.select_sentence(1))
            manager.bind("p", lambda *_: self.pause_reading())
            manager.bind("u", lambda *_: self.unpause_reading())
            manager.bind(" ", lambda *_: self.toggle_pause_reading())
            manager.bind(ptg.keys.RETURN, lambda *_: self.toggle_pause_reading())
            manager.bind(",", lambda *_: self.slower())
            manager.bind(".", lambda *_: self.faster())
            manager.bind("s", lambda *_: self.stop_gracefully(manager))
            manager.bind("q", lambda *_: self.quit_app(manager))
            manager.bind("k", lambda *_: self.scroll_view(-1))
            manager.bind("j", lambda *_: self.scroll_view(1))
            manager.bind("\x1b[5~", lambda *_: self.scroll_page(-1))
            manager.bind("\x1b[6~", lambda *_: self.scroll_page(1))
            manager.bind("g", lambda *_: self.jump_to_start())
            manager.bind("G", lambda *_: self.jump_to_end())

            manager.run()


@click.command()
@click.argument("filepath", required=False)
def tts(filepath: Optional[str]):
    if not filepath:
        click.secho("📚 BRIAN", fg="magenta")
        filepath = str(click.prompt("Enter path to a text file", type=str))

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as file:
            text = file.read()
    except FileNotFoundError:
        raise click.ClickException(f"File not found: {filepath}")

    Brian().display(text)


if __name__ == "__main__":
    warnings.simplefilter("ignore", ResourceWarning)
    tts()
