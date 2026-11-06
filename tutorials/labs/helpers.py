"""Tiny assertion helper shared by the synthetic teaching experiments."""


def check(condition: bool, name: str) -> dict:
    if not isinstance(condition, bool):
        raise TypeError("A teaching check must evaluate an actual boolean")
    if not condition:
        raise AssertionError(name)
    return {"name": name, "passed": True}
