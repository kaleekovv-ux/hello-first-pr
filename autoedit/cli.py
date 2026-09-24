"""Командная строка AutoEdit: `autoedit check` и `autoedit demo`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import align as align_module
from . import demo as demo_module
from . import env as env_module
from . import plan as plan_module
from . import render as render_module
from . import report as report_module
from . import sheet as sheet_module
from . import voice as voice_module


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
        print(f"ОШИБКА: {exc}")
        return 1

    issues = plan_module.validate_plan(data, project_dir)

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
            align_module.generate_srt(words, output_dir / "subtitles.srt")
            print(f"Субтитры сохранены: {output_dir / 'subtitles.srt'}")

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    report_path = output_dir / "report.txt"
    report_module.write_check_report(report_path, data, issues, status)

    for issue in issues:
        marker = "ОШИБКА" if issue.level == "error" else "предупреждение"
        print(f"[{marker}] {issue.format()}")

    print()
    print(f"Готово: {len(errors)} ошибок, {len(warnings)} предупреждений.")
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
        print(f"ОШИБКА: {exc}")
        return 1

    schema_issues = plan_module.validate_plan(data, project_dir)
    schema_errors = [i for i in schema_issues if i.level == "error"]
    if schema_errors:
        print("План не проходит базовую проверку — сначала исправьте это (autoedit check покажет подробности):")
        for issue in schema_errors:
            print(f"  [ОШИБКА] {issue.format()}")
        return 1

    mode = render_module.final_mode(args.vertical) if args.final else render_module.preview_mode(args.vertical)
    mode.no_fx = args.check_no_fx
    mode.no_sound = args.check_no_sound
    mode.audio_only = args.check_audio_only

    label = {
        (False, False, False): "финал" if args.final else "черновик",
        (True, False, False): "проверка без эффектов",
        (False, True, False): "проверка без звука",
        (False, False, True): "проверка только звука",
    }.get((mode.no_fx, mode.no_sound, mode.audio_only), "рендер")
    if args.vertical and not mode.audio_only:
        label += ", вертикальный 9:16"
    print(f"Собираю ролик ({label})...")

    def progress(message: str) -> None:
        print(f"  {message}")

    try:
        summary = render_module.render_project(project_dir, data, mode, args.model, progress, args.allow_reorder)
    except render_module.RenderError as exc:
        print(f"ОШИБКА: {exc}")
        return 1

    print()
    for w in summary.warnings:
        print(f"[предупреждение] {w}")
    for issue in summary.issues:
        marker = "ОШИБКА" if issue.level == "error" else "предупреждение"
        print(f"[{marker}] {issue.format()}")

    print()
    print(f"Готово: {summary.output_path}")
    if summary.report_path:
        print(f"Отчёт: {summary.report_path}")
    return 1 if any(i.level == "error" for i in summary.issues) else 0


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
        "--allow-reorder",
        action="store_true",
        help="Не останавливаться, если порядок кадров в плане не совпадает с порядком в озвучке (только предупреждение)",
    )
    render_parser.add_argument(
        "--model",
        default=align_module.DEFAULT_MODEL_SIZE,
        help=f"Размер модели распознавания речи (по умолчанию {align_module.DEFAULT_MODEL_SIZE})",
    )
    render_parser.set_defaults(func=cmd_render)

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
