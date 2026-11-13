"""Versioned cells inside records.data JSONB; legacy scalar rows remain readable.

The REST boundary encodes cells and exposes raw scalars to existing editors.
No migration/backfill or inference about omitted historical cells is performed.
"""
from .cell_policy import Cell, CellStatus, MISSING, POLICY_VERSION, normalize_cell

LABELS = {'value': '값', 'explicit_dash': '미확정(-)', 'blank': '빈칸',
          'missing': '추출 누락', 'unreadable': '판독 불가', 'unknown': '미확인',
          'ambiguous': '중복·모호'}


class CellData(dict):
    """Raw editor values plus lossless state metadata (including on deepcopy)."""
    def __init__(self, values=(), cells=None):
        super().__init__(values)
        self.cells = cells or {}


def read_cell(value):
    if isinstance(value, dict):
        if value.get('version') != POLICY_VERSION:
            raise ValueError('Unsupported stored cell version')
        cell = Cell(value['raw'], value['value'], CellStatus(value['status']))
        if cell.status != CellStatus.VALUE and cell.value is not None:
            raise ValueError('Unresolved cell cannot contain a value')
        return cell
    return normalize_cell(value)


def cells_for(data, labels=()):
    saved = getattr(data, 'cells', {})
    out = {}
    for label in dict.fromkeys([*data, *labels]):
        raw = data.get(label, MISSING)
        old = saved.get(label)
        out[label] = old if old and raw == old.raw else read_cell(raw)
    return out


def encode_record(record):
    if 'data' not in record:
        return record
    return {**record, 'data': {k: c.to_dict() for k, c in cells_for(record['data']).items()}}


def decode_record(record):
    if not record or 'data' not in record:
        return record
    cells = {k: read_cell(v) for k, v in (record['data'] or {}).items()}
    return {**record, 'data': CellData({k: c.raw for k, c in cells.items()}, cells)}


def edit_data(data, changes):
    result = CellData(data, dict(getattr(data, 'cells', {})))
    for k, v in changes.items():
        result[k] = v
        result.cells.pop(k, None)
    return result


def status_rows(records, labels=()):
    return [{'기록': r.get('id'), '칸': k, '원문': c.raw, '상태': LABELS[c.status],
             '계산 값': c.value} for r in records for k, c in cells_for(r['data'], labels).items()]


def extracted_data(cells, labels=()):
    """Keep every observed cell and explicitly record omitted declared columns."""
    saved = {}
    for c in cells:
        label = c['label'].strip()
        raw = c.get('written', c.get('value'))
        cell = normalize_cell(raw, unreadable=c.get('unreadable', False))
        if cell.status == CellStatus.VALUE:
            cell = Cell(raw, c.get('value', cell.value), cell.status)
        if label in saved:
            cell = normalize_cell([saved[label].raw, raw], ambiguous=True)
        saved[label] = cell
    for label in labels:
        saved.setdefault(label.strip(), normalize_cell())
    return CellData({k: c.raw for k, c in saved.items()}, saved)
