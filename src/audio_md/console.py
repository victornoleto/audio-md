"""Terminal output: step headers, spinners and a progress bar (rich).

This module is the SOLE writer to the terminal. The pipeline announces each phase
with ``step()`` and drives a determinate bar during transcription (advanced by the
number of audio seconds already processed) so it is always clear what stage we are in.
"""

from __future__ import annotations

from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

_out = Console()
_err = Console(stderr=True)


def info(msg: str) -> None:
    _out.print(msg)


def warn(msg: str) -> None:
    _err.print(f"[yellow]warn[/yellow] {msg}")


def note(msg: str) -> None:
    _out.print(f"[dim]· {msg}[/dim]")


def step(msg: str) -> None:
    """Announce a pipeline phase, e.g. 'Transcribing'."""
    _out.print(f"[bold cyan]▸[/bold cyan] {msg}")


@contextmanager
def status(msg: str):
    """Spinner for an indeterminate phase (model load, summarizing)."""
    with _out.status(f"[cyan]{msg}[/cyan]", spinner="dots"):
        yield


@contextmanager
def transcribe_bar(total_seconds: float):
    """Determinate progress over the audio timeline.

    Yields an ``advance(done_seconds)`` callable that takes the absolute timestamp
    (in seconds) reached so far. Falls back to an indeterminate spinner if the audio
    duration is unknown.
    """
    total = total_seconds if total_seconds and total_seconds > 0 else None
    p = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=_out,
    )
    p.start()
    task = p.add_task("transcribing", total=total)
    try:
        def advance(done_seconds: float) -> None:
            if total:
                p.update(task, completed=min(done_seconds, total))

        yield advance
        if total:
            p.update(task, completed=total)
    finally:
        p.stop()
