"""Командная строка AutoEdit: `autoedit check` и `autoedit demo`."""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import align as align_module
from . import colorlog
from . import demo as demo_module
from . import env as env_module
from . import plan as plan_module
from . import render as render_module
from . import report as report_module
from . import sheet as sheet_module
from . import voice as voice_module


def _print_render_summary(summary: render_module.RenderSummary) -> None:
    """Короткая (3-5 строк) итоговая сводка после сборки ролика."""
    issue_errors = [i for i in summary.issues if i.level == "error"]
    issue_warnings = [i for i in summary.issues if i.level == "warning"]
    total_warnings = len(summary.warnings) + len(issue_warnings)
    has_errors = bool(issue_errors)

    status_line = "ГОТОВО" if not has_errors else "ЗАВЕРШЕНО С ОШИБКАМИ"
    print(colorlog.err(status_line) if has_errors else colorlog.ok(status_line))
    print(f"Файл: {summary.output_path}")
    cache_part = f", из кэша: {summary.shots_from_cache}/{summary.shots_total}" if summary.shots_total else ""
    print(
        f"Длительность: {summary.total_duration:.1f} сек, кадров: {summary.shots_total}{cache_part}, "
        f"время сборки: {summary.render_seconds:.0f} сек"
    )
    if summary.achieved_lufs is not None:
        print(f"Громкость: {summary.achieved_lufs:.1f} LUFS")
    warn_err_line = f"Предупреждений: {total_warnings}, ошибок: {len(issue_errors)}."
    print(colorlog.warn(warn_err_line) if total_warnings and not has_errors else warn_err_line)
    if summary.report_path:
        print(f"Отчёт: {summary.report_path}")


def _print_env_status(status: env_module.EnvStatus) -> None:
    py_state = "OK" if status.python_ok else "нужна версия 3.11 или новее"
    print(f"Python: {status.python_version} ({py_state})")
    if status.ffmpeg_ok:
        print(f"FFmpeg: найден ({status.ffmpeg_version or 'версия не определена'})")
    else:
        print("FFmpeg: НЕ найден.")
        print(env_module.ffmpeg_install_hint())


def cmd_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.project).resolve()
    print(f"Проверяю проект: {project_dir}")

    status = env_module.check_environment()
    _print_env_status(status)
    print()

    plan_path = project_dir / "plan.json"
    try:
        data = plan_module.load_plan_file(plan_path)
    except plan_module.PlanError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1

    issues = plan_module.validate_plan(data, project_dir)

    script_lines = None
    if args.script:
        try:
            script_lines = voice_module.load_script(Path(args.script))
        except voice_module.VoiceError as exc:
            print(colorlog.err(f"ОШИБКА: {exc}"))
            return 1

    voice_files = data.get("voice")
    output_dir = project_dir / "output"
    if isinstance(voice_files, list) and voice_files and all(
        (project_dir / v).is_file() for v in voice_files
    ):
        print("Распознаю озвучку (faster-whisper) — при первом запуске может занять время...")
        try:
            words = align_module.load_or_transcribe(project_dir, voice_files, args.model)
        except align_module.AlignError as exc:
            issues.append(plan_module.ValidationIssue("warning", None, f"привязка к озвучке пропущена: {exc}"))
        else:
            _timeline, align_issues = align_module.build_timeline(data, words, args.allow_reorder)
            issues.extend(align_issues)
            align_module.generate_srt(words, output_dir / "subtitles.srt", script_lines)
            print(f"Субтитры сохранены: {output_dir / 'subtitles.srt'}")

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    report_path = output_dir / "report.txt"
    report_module.write_check_report(report_path, data, issues, status)

    for issue in issues:
        if issue.level == "error":
            print(colorlog.err(f"[ОШИБКА] {issue.format()}"))
        else:
            print(colorlog.warn(f"[предупреждение] {issue.format()}"))

    print()
    summary_line = f"Готово: {len(errors)} ошибок, {len(warnings)} предупреждений."
    print(colorlog.err(summary_line) if errors else colorlog.ok(summary_line))
    print(f"Отчёт сохранён: {report_path}")

    return 1 if errors else 0


def cmd_demo(args: argparse.Namespace) -> int:
    target_dir = Path(args.target).resolve()

    status = env_module.check_environment()
    if not status.ffmpeg_ok:
        print("Не могу создать демо-проект: не найден FFmpeg.")
        print(env_module.ffmpeg_install_hint())
        return 1

    print(f"Создаю демо-проект в {target_dir} ...")
    try:
        demo_module.generate_demo_project(target_dir)
    except demo_module.DemoError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    print("Демо-проект готов. Проверьте его командой:")
    print(f"  autoedit check {target_dir}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    project_dir = Path(args.project).resolve()

    status = env_module.check_environment()
    if not status.ffmpeg_ok:
        print("Не могу собрать ролик: не найден FFmpeg.")
        print(env_module.ffmpeg_install_hint())
        return 1

    plan_path = project_dir / "plan.json"
    try:
        data = plan_module.load_plan_file(plan_path)
    except plan_module.PlanError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1

    schema_issues = plan_module.validate_plan(data, project_dir)
    schema_errors = [i for i in schema_issues if i.level == "error"]
    if schema_errors:
        print(colorlog.err("План не проходит базовую проверку — сначала исправьте это (autoedit check покажет подробности):"))
        for issue in schema_errors:
            print(colorlog.err(f"  [ОШИБКА] {issue.format()}"))
        return 1

    mode = render_module.final_mode(args.vertical) if args.final else render_module.preview_mode(args.vertical)
    mode.no_fx = args.check_no_fx
    mode.no_sound = args.check_no_sound
    mode.audio_only = args.check_audio_only
    mode.debug = args.debug

    label = {
        (False, False, False): "финал" if args.final else "черновик",
        (True, False, False): "проверка без эффектов",
        (False, True, False): "проверка без звука",
        (False, False, True): "проверка только звука",
    }.get((mode.no_fx, mode.no_sound, mode.audio_only), "рендер")
    if args.vertical and not mode.audio_only:
        label += ", вертикальный 9:16"
    if mode.debug and not mode.audio_only:
        label += ", с debug-меткой на кадрах"
    print(f"Собираю ролик ({label})...")

    script_lines = None
    if args.script:
        try:
            script_lines = voice_module.load_script(Path(args.script))
        except voice_module.VoiceError as exc:
            print(colorlog.err(f"ОШИБКА: {exc}"))
            return 1

    def progress(message: str) -> None:
        print(f"  {message}")

    try:
        summary = render_module.render_project(
            project_dir, data, mode, args.model, progress, args.allow_reorder, script_lines
        )
    except render_module.RenderError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1

    print()
    for w in summary.warnings:
        print(colorlog.warn(f"[предупреждение] {w}"))
    for issue in summary.issues:
        if issue.level == "error":
            print(colorlog.err(f"[ОШИБКА] {issue.format()}"))
        else:
            print(colorlog.warn(f"[предупреждение] {issue.format()}"))

    print()
    _print_render_summary(summary)
    return 1 if any(i.level == "error" for i in summary.issues) else 0


_SHOT_PROGRESS_RE = re.compile(r"Кадр (\d+)/(\d+)")


def _make_progress_printer(prefix: str):
    def progress(message: str) -> None:
        match = _SHOT_PROGRESS_RE.search(message)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            percent = round(100 * done / total)
            print(f"  [{prefix}] {percent:3d}% — {message}")
        else:
            print(f"  [{prefix}] {message}")

    return progress


def cmd_build(args: argparse.Namespace) -> int:
    """check -> preview -> (если без ошибок) final, одной командой."""
    project_dir = Path(args.project).resolve()
    overall_start = time.monotonic()

    print(colorlog.bold(f"=== AutoEdit build: {project_dir} ==="))

    print()
    print(colorlog.bold("[1/3] Проверка плана..."))
    check_ns = argparse.Namespace(
        project=str(project_dir), model=args.model, allow_reorder=args.allow_reorder, script=args.script
    )
    if cmd_check(check_ns) != 0:
        print()
        print(colorlog.err("Проверка не пройдена — сборка остановлена. Сначала исправьте ошибки выше."))
        return 1

    status = env_module.check_environment()
    if not status.ffmpeg_ok:
        print(colorlog.err("Не могу собрать ролик: не найден FFmpeg."))
        print(env_module.ffmpeg_install_hint())
        return 1

    plan_path = project_dir / "plan.json"
    try:
        data = plan_module.load_plan_file(plan_path)
    except plan_module.PlanError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1

    script_lines = None
    if args.script:
        try:
            script_lines = voice_module.load_script(Path(args.script))
        except voice_module.VoiceError as exc:
            print(colorlog.err(f"ОШИБКА: {exc}"))
            return 1

    print()
    print(colorlog.bold("[2/3] Черновая сборка (preview)..."))
    try:
        preview_summary = render_module.render_project(
            project_dir, data, render_module.preview_mode(args.vertical), args.model,
            _make_progress_printer("preview"), args.allow_reorder, script_lines,
        )
    except render_module.RenderError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1
    print()
    _print_render_summary(preview_summary)

    if any(i.level == "error" for i in preview_summary.issues):
        print()
        print(colorlog.err(
            "В черновике есть ошибки — финальная сборка не запускается. "
            "Посмотрите preview.mp4 и отчёт выше, исправьте план и запустите build заново."
        ))
        return 1

    print()
    print(colorlog.bold("[3/3] Финальная сборка (final)..."))
    try:
        final_summary = render_module.render_project(
            project_dir, data, render_module.final_mode(args.vertical), args.model,
            _make_progress_printer("final"), args.allow_reorder, script_lines,
        )
    except render_module.RenderError as exc:
        print(colorlog.err(f"ОШИБКА: {exc}"))
        return 1
    print()
    _print_render_summary(final_summary)

    has_errors = any(i.level == "error" for i in final_summary.issues)
    elapsed_min = (time.monotonic() - overall_start) / 60
    print()
    print(colorlog.err("СБОРКА ЗАВЕРШЕНА С ОШИБКАМИ") if has_errors else colorlog.ok("СБОРКА ЗАВЕРШЕНА"))
    print(f"Общее время: {elapsed_min:.1f} мин")
    return 1 if has_errors else 0


def cmd_gui(_args: argparse.Namespace) -> int:
    from . import gui as gui_module

    try:
        gui_module.run()
    except gui_module.GuiError as exc:
        print(f"ОШИБКА: {exc}")
        return 1
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    folder = Path(args.folder).resolve()
    out_dir = Path(args.out).resolve() if args.out else folder / "output"

    status = env_module.check_environment()
    if not status.ffmpeg_ok:
        print("Не могу построить лист миниатюр: не найден FFmpeg.")
        print(env_module.ffmpeg_install_hint())
        return 1

    try:
        sheets = sheet_module.generate_contact_sheets(folder, out_dir)
    except sheet_module.SheetError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    print(f"Готово: {len(sheets)} лист(а/ов) миниатюр")
    for p in sheets:
        print(f"  {p}")
    return 0


def cmd_voice(args: argparse.Namespace) -> int:
    project_dir = Path(args.project).resolve()
    script_path = Path(args.script).resolve()
    chunks_dir = Path(args.from_folder).resolve()

    status = env_module.check_environment()
    if not status.ffmpeg_ok:
        print("Не могу собрать озвучку: не найден FFmpeg.")
        print(env_module.ffmpeg_install_hint())
        return 1

    try:
        script_lines = voice_module.load_script(script_path)
        chunk_paths = voice_module.find_audio_chunks(chunks_dir)
    except voice_module.VoiceError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    print(f"Фраз в сценарии: {len(script_lines)}. Кусков озвучки: {len(chunk_paths)}.")
    print("Распознаю куски (faster-whisper) — при первом запуске может занять время...")
    try:
        chunk_texts = voice_module.transcribe_chunks(chunk_paths, args.model)
    except voice_module.VoiceError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    report = voice_module.match_chunks_to_script(script_lines, chunk_texts)

    print()
    print("Фраза сценария -> файл:")
    for m in report.matches:
        short_line = m.script_line if len(m.script_line) <= 60 else m.script_line[:57] + "..."
        chunk_name = m.chunk.path.name if m.chunk else "ОТСУТСТВУЕТ — нет озвучки для этой фразы"
        print(f'  "{short_line}" -> {chunk_name}')

    if report.duplicates:
        print()
        print("Дубли (взят самый поздний по времени в имени файла, остальные не используются):")
        for line, path in report.duplicates:
            short_line = line if len(line) <= 60 else line[:57] + "..."
            print(f'  "{short_line}": {path.name}')

    if report.unused_chunks:
        print()
        print("Куски без совпадения в сценарии (не будут использованы):")
        for p in report.unused_chunks:
            print(f"  {p.name}")

    missing = [m for m in report.matches if m.chunk is None]
    if missing:
        print()
        print(f"Внимание: для {len(missing)} фраз(ы) сценария не нашлось озвучки — они не попадут в voice/full.mp3.")

    matched_paths = [m.chunk.path for m in report.matches if m.chunk]
    if not matched_paths:
        print("\nОШИБКА: ни одна фраза сценария не была озвучена, склеивать нечего.")
        return 1

    if not args.yes:
        answer = input("\nСклеить voice/full.mp3 по этому сопоставлению? [y/N]: ").strip().lower()
        if answer not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    out_path = project_dir / "voice" / "full.mp3"
    try:
        voice_module.concatenate_chunks(matched_paths, out_path)
    except voice_module.VoiceError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    print(f"\nГотово: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoedit",
        description="AutoEdit — черновой автомонтаж документальных роликов по плану.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Проверить план и файлы проекта")
    check_parser.add_argument("project", help="Путь к папке проекта (с plan.json)")
    check_parser.add_argument(
        "--model",
        default=align_module.DEFAULT_MODEL_SIZE,
        help=f"Размер модели распознавания речи (по умолчанию {align_module.DEFAULT_MODEL_SIZE})",
    )
    check_parser.add_argument(
        "--allow-reorder",
        action="store_true",
        help="Не считать ошибкой, если порядок кадров в плане не совпадает с порядком в озвучке (только предупреждение)",
    )
    check_parser.add_argument(
        "--script",
        help="Файл сценария (по фразе на строку) — если указан, субтитры для точно совпавших "
        "реплик будут показывать текст из сценария (например, с цифрами вроде «438»), а не то, "
        "что распознал whisper",
    )
    check_parser.set_defaults(func=cmd_check)

    demo_parser = subparsers.add_parser("demo", help="Создать тестовый проект с фальшивыми ассетами")
    demo_parser.add_argument(
        "target",
        nargs="?",
        default="./autoedit_demo_project",
        help="Куда создать демо-проект (по умолчанию ./autoedit_demo_project)",
    )
    demo_parser.set_defaults(func=cmd_demo)

    render_parser = subparsers.add_parser("render", help="Собрать ролик по плану")
    render_parser.add_argument("project", help="Путь к папке проекта (с plan.json)")
    render_parser.add_argument("--final", action="store_true", help="Финальное качество (1920x1080, медленнее) вместо чернового превью")
    render_parser.add_argument(
        "--vertical",
        action="store_true",
        help="Вертикальный формат 9:16 (для YouTube Shorts/TikTok/Reels) вместо обычного 16:9 — "
        "картинка обрезается по центру из тех же кадров плана",
    )
    render_parser.add_argument("--check-no-fx", action="store_true", help="Без эффектов и графики — держится ли история на кадрах и голосе")
    render_parser.add_argument("--check-no-sound", action="store_true", help="Без звука — понятно ли по картинке")
    render_parser.add_argument("--check-audio-only", action="store_true", help="Только звук — держится ли история на голосе и звуке")
    render_parser.add_argument(
        "--debug",
        action="store_true",
        help="В углу каждого кадра — id, таймкод и anchor (если есть), чтобы легче искать проблемные места в черновике",
    )
    render_parser.add_argument(
        "--allow-reorder",
        action="store_true",
        help="Не останавливаться, если порядок кадров в плане не совпадает с порядком в озвучке (только предупреждение)",
    )
    render_parser.add_argument(
        "--model",
        default=align_module.DEFAULT_MODEL_SIZE,
        help=f"Размер модели распознавания речи (по умолчанию {align_module.DEFAULT_MODEL_SIZE})",
    )
    render_parser.add_argument(
        "--script",
        help="Файл сценария (по фразе на строку) — если указан, субтитры для точно совпавших "
        "реплик будут показывать текст из сценария (например, с цифрами вроде «438»), а не то, "
        "что распознал whisper",
    )
    render_parser.set_defaults(func=cmd_render)

    build_parser_cmd = subparsers.add_parser(
        "build", help="Собрать ролик полностью: проверка -> черновик -> финал одной командой"
    )
    build_parser_cmd.add_argument("project", help="Путь к папке проекта (с plan.json)")
    build_parser_cmd.add_argument(
        "--vertical",
        action="store_true",
        help="Вертикальный формат 9:16 вместо обычного 16:9 (для preview и final)",
    )
    build_parser_cmd.add_argument(
        "--allow-reorder",
        action="store_true",
        help="Не останавливаться, если порядок кадров в плане не совпадает с порядком в озвучке (только предупреждение)",
    )
    build_parser_cmd.add_argument(
        "--model",
        default=align_module.DEFAULT_MODEL_SIZE,
        help=f"Размер модели распознавания речи (по умолчанию {align_module.DEFAULT_MODEL_SIZE})",
    )
    build_parser_cmd.add_argument(
        "--script",
        help="Файл сценария (по фразе на строку) — субтитры для точно совпавших реплик покажут "
        "текст из сценария (например, с цифрами вроде «438»)",
    )
    build_parser_cmd.set_defaults(func=cmd_build)

    gui_parser = subparsers.add_parser("gui", help="Открыть простое графическое окно (необязательно)")
    gui_parser.set_defaults(func=cmd_gui)

    sheet_parser = subparsers.add_parser("sheet", help="Собрать лист миниатюр по видео в папке")
    sheet_parser.add_argument("folder", help="Папка с видеофайлами (ищет и во вложенных папках)")
    sheet_parser.add_argument("--out", help="Куда сохранить contact_sheet.jpg (по умолчанию <папка>/output)")
    sheet_parser.set_defaults(func=cmd_sheet)

    voice_parser = subparsers.add_parser("voice", help="Склеить озвучку из кусков по сценарию")
    voice_parser.add_argument("project", help="Путь к папке проекта (озвучка сохранится в <проект>/voice/full.mp3)")
    voice_parser.add_argument("--script", required=True, help="Файл сценария (по фразе на строку)")
    voice_parser.add_argument("--from", dest="from_folder", required=True, help="Папка с отдельными кусками озвучки")
    voice_parser.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение перед склейкой")
    voice_parser.add_argument(
        "--model",
        default=align_module.DEFAULT_MODEL_SIZE,
        help=f"Размер модели распознавания речи (по умолчанию {align_module.DEFAULT_MODEL_SIZE})",
    )
    voice_parser.set_defaults(func=cmd_voice)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
