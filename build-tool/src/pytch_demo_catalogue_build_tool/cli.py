"""Console scripts for pytchbuild"""

import click
from click_loglevel import LogLevel
from pathlib import Path
import colorlog
import pytch_demo_catalogue_build_tool.version_data
import pytch_demo_catalogue_build_tool.new_demo
import pytch_demo_catalogue_build_tool.update_demo_project


def configure_logging(log_level: int):
    log_handler = colorlog.StreamHandler()
    log_handler.setFormatter(
        colorlog.ColoredFormatter("%(log_color)s%(levelname)s : %(message)s")
    )

    logger = colorlog.getLogger()  # Root logger
    logger.addHandler(log_handler)
    logger.setLevel(log_level)


def dir_argument():
    return click.Path(file_okay=False, dir_okay=True)


def log_level_option():
    return click.option(
        "--log-level",
        type=LogLevel(),
        default="WARNING",
        help="Set logging level",
        show_default=True,
    )


@click.command()
@click.argument("demos-repo", type=dir_argument())
@click.argument("dist-root", type=dir_argument())
@click.option(
    "--start-ref",
    default=None,
    help=(
        "Revision (branch, tag, or SHA1) whose history is analysed;"
        " default is HEAD."
    ),
)
@log_level_option()
def build_dist(demos_repo: str, dist_root: str, start_ref: str | None, log_level: int):
    configure_logging(log_level)
    pytch_demo_catalogue_build_tool.version_data.main(
        Path(demos_repo),
        Path(dist_root),
        start_ref,
    )


@click.command()
@click.argument("new-demo-dirname", type=str)
@click.argument("locale", type=str)
@click.argument("project-zipfile", type=click.Path(dir_okay=False, exists=True))
@log_level_option()
def new_demo(
    new_demo_dirname: str,
    locale: str,
    project_zipfile: str,
    log_level: int,
):
    configure_logging(log_level)
    pytch_demo_catalogue_build_tool.new_demo.main(
        Path(new_demo_dirname), locale, Path(project_zipfile)
    )


@click.command()
@click.argument("demo-dirname", type=str)
@click.argument("locale", type=str)
@click.argument("project-zipfile", type=click.Path(dir_okay=False, exists=True))
@log_level_option()
def update_demo(
    demo_dirname: str,
    locale: str,
    project_zipfile: str,
    log_level: int,
):
    configure_logging(log_level)
    pytch_demo_catalogue_build_tool.update_demo_project.main(
        Path(demo_dirname), locale, Path(project_zipfile)
    )
