"""Console scripts for pytchbuild"""

import click


def dir_argument():
    return click.Path(file_okay=False, dir_okay=True)
