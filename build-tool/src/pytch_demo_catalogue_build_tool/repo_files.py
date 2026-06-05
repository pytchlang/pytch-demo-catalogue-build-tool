import pygit2


def name_of_tree_entry(entry: pygit2.Object) -> str:
    if entry.name is None:
        raise RuntimeError(f"Object {entry.id} has no name")
    return entry.name
