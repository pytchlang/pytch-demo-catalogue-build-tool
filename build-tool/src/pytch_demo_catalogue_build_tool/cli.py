"""Console scripts for pytchbuild"""

import click
from pathlib import Path
import pytch_demo_catalogue_build_tool.version_data


def dir_argument():
    return click.Path(file_okay=False, dir_okay=True)


@click.command()
@click.argument("demos-repo", type=dir_argument())
@click.argument("dist-root", type=dir_argument())
def build_dist(demos_repo: str, dist_root: str):
    pytch_demo_catalogue_build_tool.version_data.main(Path(demos_repo), Path(dist_root))
