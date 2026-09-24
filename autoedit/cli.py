"""Командная строка AutoEdit: `autoedit check` и `autoedit demo`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import demo as demo_module
from . import env as env_module
from . import plan as plan_module
from . import report as report_module


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
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    output_dir = project_dir / "output"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoedit",
        description="AutoEdit — черновой автомонтаж документальных роликов по плану.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Проверить план и файлы проекта")
    check_parser.add_argument("project", help="Путь к папке проекта (с plan.json)")
    check_parser.set_defaults(func=cmd_check)

    demo_parser = subparsers.add_parser("demo", help="Создать тестовый проект с фальшивыми ассетами")
    demo_parser.add_argument(
        "target",
        nargs="?",
        default="./autoedit_demo_project",
        help="Куда создать демо-проект (по умолчанию ./autoedit_demo_project)",
    )
    demo_parser.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
