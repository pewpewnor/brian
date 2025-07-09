#!/usr/bin/env python3

import getpass
import os
import platform
import site
import sys

# elevate to root
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
    else:
        return None


# add user site-packages path (cross-platform)
user_site = get_user_site_path(real_user)
if user_site and os.path.isdir(user_site):
    site.addsitedir(user_site)

# pulseaudio and pipewire env setup (only on Linux)
if platform.system() == "Linux":
    user_uid = os.popen(f"id -u {real_user}").read().strip()
    xdg_runtime_dir = f"/run/user/{user_uid}"
    os.environ["XDG_RUNTIME_DIR"] = xdg_runtime_dir
    os.environ["PULSE_SERVER"] = f"unix:{xdg_runtime_dir}/pulse/native"

import re
import threading
import warnings
from enum import Enum
from typing import List, Optional

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


import threading
import time
from enum import Enum
from typing import List, Optional

import pytermgui as ptg
import pyttsx3


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
        self.paragraph_index = 0
        self.sentence_index = 0
        self.view_start = 0
        self.view_height = 10

        self.reading_active = False
        self.stopped = False
        self.speech_wpm = 170
        self.current_thread: Optional[threading.Thread] = None

        self.content_label = ptg.Label("", parent_align=ptg.HorizontalAlignment.LEFT)

        self.status_text_label = ptg.Label()
        self.wpm_label = ptg.Label()

        self.update_status_label(StatusLabel.PAUSED)
        self.update_speech_wpm()

        self.engine.setProperty("rate", self.speech_wpm)

    def display(self, text: str):
        self.paragraphs = parse_text(text)
        self.update_content_view()
        self.run_ui()

    def update_status_label(self, status_label: StatusLabel):
        self.status_text_label.value = status_label.value

    def update_speech_wpm(self):
        self.wpm_label.value = f"[bold magenta]{self.speech_wpm} wpm[/bold]"

    def update_content_view(self):
        visible = self.paragraphs[self.view_start : self.view_start + self.view_height]
        lines = []

        for i, paragraph in enumerate(visible):
            para_line = ""
            abs_i = self.view_start + i
            for j, sentence in enumerate(paragraph):
                if abs_i < self.paragraph_index or (
                    abs_i == self.paragraph_index and j < self.sentence_index
                ):
                    para_line += f"[dim white]{sentence}[/dim white] "
                elif abs_i == self.paragraph_index and j == self.sentence_index:
                    para_line += (
                        f"[bold underline white]{sentence}[/bold /underline white] "
                    )
                else:
                    para_line += f"[white]{sentence}[white] "

            lines.append(para_line.strip())

        self.content_label.value = "\n\n".join(lines)

    def select_paragraph(self, delta: int):
        if self.reading_active:
            return

        self.paragraph_index = max(
            0, min(len(self.paragraphs) - 1, self.paragraph_index + delta)
        )
        self.sentence_index = 0

        if self.paragraph_index < self.view_start:
            self.view_start = self.paragraph_index
        elif self.paragraph_index >= self.view_start + self.view_height:
            self.view_start = self.paragraph_index - self.view_height + 1

        self.update_content_view()

    def select_sentence(self, delta: int):
        if self.reading_active:
            return

        paragraph = self.paragraphs[self.paragraph_index]
        new_index = self.sentence_index + delta

        if new_index < 0 and self.paragraph_index > 0:
            self.select_paragraph(-1)
            self.sentence_index = len(self.paragraphs[self.paragraph_index]) - 1
        elif (
            new_index >= len(paragraph)
            and self.paragraph_index < len(self.paragraphs) - 1
        ):
            self.select_paragraph(1)
            self.sentence_index = 0
        else:
            self.sentence_index = max(0, min(len(paragraph) - 1, new_index))

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
        global sentence_finished

        def speaker_loop():
            # needed 2 params here, otherwise it wont work
            def on_end(name, completed):
                sentence_finished.set()

            self.engine.connect("finished-utterance", on_end)

            while not self.stopped:
                i = self.paragraph_index
                j = self.sentence_index

                if i >= len(self.paragraphs):
                    self.reading_active = False
                    self.update_status_label(StatusLabel.PAUSED)
                    self.update_content_view()
                    return

                para = self.paragraphs[i]

                while j < len(para) and not self.stopped:
                    if not self.reading_active:
                        time.sleep(0.1)
                        i = self.paragraph_index
                        j = self.sentence_index
                        if i >= len(self.paragraphs):
                            break
                        para = self.paragraphs[i]
                        continue

                    self.paragraph_index = i
                    self.sentence_index = j
                    self.update_content_view()

                    sentence_finished = threading.Event()

                    with self.engine_lock:
                        self.engine.say(para[j])
                        self.engine.runAndWait()

                    sentence_finished.wait()

                    if (
                        self.paragraph_index == i
                        and self.sentence_index == j
                        and self.reading_active
                    ):
                        j += 1
                        if j >= len(para):
                            i += 1
                            j = 0
                            if i >= len(self.paragraphs):
                                self.reading_active = False
                                self.update_status_label(StatusLabel.PAUSED)
                                self.update_content_view()
                                return
                            para = self.paragraphs[i]
                    else:
                        i = self.paragraph_index
                        j = self.sentence_index
                        if i < len(self.paragraphs):
                            para = self.paragraphs[i]

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

    def scroll_view(self, delta: int):
        max_start = max(0, len(self.paragraphs) - self.view_height)
        self.view_start = max(0, min(max_start, self.view_start + delta))
        self.update_content_view()

    def run_ui(self):
        with ptg.WindowManager() as manager:
            manager.layout.add_slot("Body")

            title = ptg.Label(
                "[bold]BRIAN[/bold]",
                justify="center",
            )
            instructions = ptg.Label(
                "[dim]↑↓ [Paragraph]  ←→ [Sentence] j/k [Scroll Paragraph]  p [Pause]  u [Unpause]  space/⏎ [Toggle]  ,/. [Speed]  s [Stop]  q [Quit][/dim]",
                justify="center",
            )
            separator = ptg.Label("─" * (ptg.Terminal().width - 10), style="dim")

            status_bar = ptg.Splitter(
                self.status_text_label,
                self.wpm_label,
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
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as file:
                text = file.read()
        except FileNotFoundError:
            raise click.ClickException(f"File not found: {filepath}")

    Brian().display(text)


if __name__ == "__main__":
    warnings.simplefilter("ignore", ResourceWarning)
    tts()
