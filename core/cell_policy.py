"""Versioned, lossless cell-state policy. No model, database or UI dependencies.

None is unknown, not evidence of a visually blank cell. Unreadable must be
explicitly reported by extraction/review; text such as '?' is never guessed.
Only the literal ASCII '-' (with surrounding whitespace allowed) is a dash.
Numeric parsing/units are the caller's responsibility; zero remains a value.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import math

POLICY_VERSION = 'cell-state-v1'
MISSING = object()


class CellStatus(str, Enum):
    VALUE = 'value'
    EXPLICIT_DASH = 'explicit_dash'
    BLANK = 'blank'
    MISSING = 'missing'
    UNREADABLE = 'unreadable'
    UNKNOWN = 'unknown'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True)
class Cell:
    raw: object
    value: object
    status: CellStatus

    def to_dict(self):
        return dict(version=POLICY_VERSION, **asdict(self))


def normalize_cell(raw=MISSING, *, unreadable=False, ambiguous=False):
    if unreadable and ambiguous:
        raise ValueError('Conflicting cell evidence')
    if raw is MISSING:
        if unreadable or ambiguous:
            raise ValueError('Missing cell cannot carry observation flags')
        return Cell(None, None, CellStatus.MISSING)
    if ambiguous:
        return Cell(raw, None, CellStatus.AMBIGUOUS)
    if unreadable:
        return Cell(raw, None, CellStatus.UNREADABLE)
    if raw is None:
        return Cell(None, None, CellStatus.UNKNOWN)
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError('Cell must contain a string, finite number, or null')
    if isinstance(raw, float) and not math.isfinite(raw):
        raise ValueError('Non-finite cell number')
    value = raw.strip() if isinstance(raw, str) else raw
    if value == '':
        return Cell(raw, None, CellStatus.BLANK)
    if value == '-':
        return Cell(raw, None, CellStatus.EXPLICIT_DASH)
    return Cell(raw, value, CellStatus.VALUE)


def cells_match(expected, actual, canonicalize=lambda value: value):
    """Missing/unknown/ambiguous/unreadable never establish a correct reading."""
    if expected.status != actual.status:
        return False
    if expected.status in (CellStatus.BLANK, CellStatus.EXPLICIT_DASH):
        return True
    return (expected.status == CellStatus.VALUE
            and canonicalize(expected.value) == canonicalize(actual.value))
