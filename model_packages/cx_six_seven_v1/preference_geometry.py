"""Three-dimensional geometry used by six/seven preference projections."""
import math


def _magnitude(vector):
    return math.sqrt(
        vector["x"] ** 2 + vector["y"] ** 2 + vector["z"] ** 2
    )


def cosine_similarity(left, right):
    left_magnitude = _magnitude(left)
    right_magnitude = _magnitude(right)
    if left_magnitude == 0 or right_magnitude == 0:
        return 0.0
    dot = (
        left["x"] * right["x"]
        + left["y"] * right["y"]
        + left["z"] * right["z"]
    )
    return round(dot / (left_magnitude * right_magnitude), 3)


__all__ = ("cosine_similarity",)
