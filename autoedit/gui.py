"""Необязательный минимальный графический интерфейс (этап 7).

Окно: выбрать папку проекта, кнопки «Проверить» / «Черновик» / «Финал» /
«Собрать всё», полоса прогресса, лог хода работы и кнопка «Открыть
отчёт». Использует только стандартную библиотеку Python (tkinter),
поэтому не требует установки чего-либо сверх самого AutoEdit — но сам
tkinter должен быть в вашей установке Python (в официальных сборках
с python.org для Windows и macOS он есть по умолчанию).

Эта часть программы не тестировалась автоматически (для GUI это и
не нужно, но имейте в виду, что бывают мелкие шероховатости) — если
что-то работает не так, как ожидается, откройте issue или используйте
обычную командную строку (autoedit check / autoedit render / autoedit build).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:  # pragma: no cover - зависит от сборки Python
    tk = None  # type: ignore[assignment]

_SHOT_PROGRESS_RE = re.compile(r"Кадр (\d+)/(\d+)")


class GuiError(Exception):
    """Tkinter недоступен в этой установке Python."""


def _run_cli_in_thread(
    args: list[str], log_widget: "scrolledtext.ScrolledText", progress_bar: "ttk.Progressbar", on_done
) -> None:
    def worker() -> None:
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "autoedit.cli", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                log_widget.after(0, _append_log, log_widget, progress_bar, line)
            process.wait()
        finally:
            log_widget.after(0, on_done)

    threading.Thread(target=worker, daemon=True).start()


def _append_log(log_widget: "scrolledtext.ScrolledText", progress_bar: "ttk.Progressbar", text: str) -> None:
    log_widget.configure(state="normal")
    log_widget.insert(tk.END, text)
    log_widget.see(tk.END)
    log_widget.configure(state="disabled")

    match = _SHOT_PROGRESS_RE.search(text)
    if match:
        done, total = int(match.group(1)), int(match.group(2))
        progress_bar["value"] = 100 * done / total if total else 0


def run() -> None:
    if tk is None:
        raise GuiError(
            "В этой установке Python нет модуля tkinter. "
            "Переустановите Python с официального сайта python.org (он включает tkinter) "
            "или используйте обычную командную строку: autoedit check / autoedit render."
        )

    root = tk.Tk()
    root.title("AutoEdit")
    root.geometry("640x420")

    project_var = tk.StringVar(value=str(Path.cwd()))

    top_frame = tk.Frame(root, padx=8, pady=8)
    top_frame.pack(fill=tk.X)

    tk.Label(top_frame, text="Папка проекта:").pack(side=tk.LEFT)
    entry = tk.Entry(top_frame, textvariable=project_var)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

    def choose_folder() -> None:
        chosen = filedialog.askdirectory(initialdir=project_var.get() or ".")
        if chosen:
            project_var.set(chosen)

    tk.Button(top_frame, text="Выбрать...", command=choose_folder).pack(side=tk.LEFT)

    buttons_frame = tk.Frame(root, padx=8, pady=4)
    buttons_frame.pack(fill=tk.X)

    progress = ttk.Progressbar(root, orient="horizontal", mode="determinate", maximum=100)
    progress.pack(fill=tk.X, padx=8)

    log = scrolledtext.ScrolledText(root, state="disabled", height=18)
    log.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    buttons: list[tk.Button] = []

    def set_buttons_enabled(enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in buttons:
            b.configure(state=state)

    def start(args: list[str]) -> None:
        project = project_var.get().strip()
        if not project:
            messagebox.showerror("AutoEdit", "Сначала укажите папку проекта")
            return
        log.configure(state="normal")
        log.delete("1.0", tk.END)
        log.configure(state="disabled")
        progress["value"] = 0
        set_buttons_enabled(False)

        def on_done() -> None:
            set_buttons_enabled(True)

        _run_cli_in_thread([*args, project], log, progress, on_done)

    def open_report() -> None:
        output_dir = Path(project_var.get()) / "output"
        candidates = sorted(output_dir.glob("report*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            messagebox.showinfo("AutoEdit", "Отчёт ещё не создан — сначала запустите «Проверить» или сборку ролика.")
            return
        report_path = candidates[0]
        if sys.platform == "win32":
            os.startfile(report_path)  # noqa: S606 - открытие файла в системном приложении
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(report_path)])
        else:
            subprocess.Popen(["xdg-open", str(report_path)])

    buttons.append(tk.Button(buttons_frame, text="Проверить", command=lambda: start(["check"])))
    buttons.append(tk.Button(buttons_frame, text="Черновик", command=lambda: start(["render"])))
    buttons.append(tk.Button(buttons_frame, text="Финал", command=lambda: start(["render", "--final"])))
    buttons.append(tk.Button(buttons_frame, text="Собрать всё", command=lambda: start(["build"])))
    for b in buttons:
        b.pack(side=tk.LEFT, padx=4)

    tk.Button(buttons_frame, text="Открыть отчёт", command=open_report).pack(side=tk.RIGHT, padx=4)

    root.mainloop()


if __name__ == "__main__":
    run()
