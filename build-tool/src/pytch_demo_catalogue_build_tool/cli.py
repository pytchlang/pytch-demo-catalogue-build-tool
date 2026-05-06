"""Console scripts for pytchbuild"""

import click
from click_loglevel import LogLevel
from pathlib import Path
import colorlog
import pytch_demo_catalogue_build_tool.version_data
import pytch_demo_catalogue_build_tool.new_demo


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
def build_dist(demos_repo: str, dist_root: str):
    pytch_demo_catalogue_build_tool.version_data.main(Path(demos_repo), Path(dist_root))


@click.command()
@click.argument("new-demo-dirname", type=str)
@click.argument("locale", type=str)
@click.argument("project-zipfile", type=click.Path(dir_okay=False, exists=True))
def new_demo(new_demo_dirname: str, locale: str, project_zipfile: str):
    pytch_demo_catalogue_build_tool.new_demo.main(
        Path(new_demo_dirname), locale, Path(project_zipfile)
    )
