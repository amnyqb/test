"""The use cases implemented so far, by id."""

from feasibility.cases import g04_identity, g06_structural

USE_CASES = {case.id: case for case in (g04_identity.USE_CASE, g06_structural.USE_CASE)}
