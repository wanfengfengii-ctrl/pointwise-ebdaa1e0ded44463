"""Preview and confirm the merge of two correction drafts of one fixed parent.

The preview is a pure reading of the two drafts together with their shared
parent: it never changes the drafts or the current edition.  The confirm step
replays a frozen preview into a new edition and its receipt.
"""
from collections.abc import Iterable
from heapq import heapify, heappop, heappush
from sqlite3 import Connection

from warehouse.drafts.store import DraftRow, WorkingDay, draft_revision, load_draft, moved_originals
from warehouse.intake.observations import encode_reading
from warehouse.intake.validation import validate_record_id
from warehouse.publication.editions import Edition, load_edition


Ref = tuple[str, int]

# The conflict categories reported by MERGE.md.
READING = 'reading'
REMOVE_CORRECT = 'remove_correct'
REMOVE_MOVE = 'remove_move'
ORDER = 'order'


def _require_merge_capable(db: Connection, draft: WorkingDay) -> None:
    if draft.base.parent_id is None:
        raise ValueError('only fixed-parent drafts may be merged')
    capable = db.execute(
        'SELECT 1 FROM merge_capable_drafts WHERE draft_id = ?', (draft.draft_id,),
    ).fetchone()
    if capable is None:
        raise ValueError('draft was created before the merge feature and cannot be merged')


def _original_rows(draft: WorkingDay) -> dict[int, DraftRow]:
    return {row.parent_position: row for row in draft.rows if row.parent_position is not None}


def _supplement_rows(draft: WorkingDay) -> list[tuple[int, DraftRow]]:
    return [(index, row) for index, row in enumerate(draft.rows, 1) if row.parent_position is None]


def _original_rank(draft: WorkingDay) -> dict[int, int]:
    """Map each surviving original's parent position to its rank in the draft."""
    return {
        row.parent_position: rank
        for rank, row in enumerate(row for row in draft.rows if row.parent_position is not None)
    }


def _active_relocations(draft: WorkingDay, moved: frozenset[int]) -> set[int]:
    """Explicitly moved originals whose final order is inverted against a peer.

    The inversion is judged on the draft's final original order, and only rows
    still present in this draft take part in it.
    """
    positions = [row.parent_position for row in draft.rows if row.parent_position is not None]
    active: set[int] = set()
    for here, parent_position in enumerate(positions):
        if parent_position not in moved:
            continue
        for there, other in enumerate(positions):
            if other == parent_position:
                continue
            parent_before = parent_position < other
            draft_before = here < there
            if parent_before != draft_before:
                active.add(parent_position)
                break
    return active


def _value_conflict(
    parent: Edition, left: WorkingDay, right: WorkingDay,
) -> dict[str, object] | None:
    left_rows = _original_rows(left)
    right_rows = _original_rows(right)
    for parent_row in parent.rows:
        position = parent_row.position
        if position in left_rows and position in right_rows:
            parent_liters = parent_row.observation.liters
            left_liters = left_rows[position].observation.liters
            right_liters = right_rows[position].observation.liters
            # A correction is a value change against the parent; only two
            # differing corrections of the same original clash.
            if (left_liters != parent_liters and right_liters != parent_liters
                    and left_liters != right_liters):
                return {'kind': READING, 'refs': (('parent', position),)}
    return None


def _withdrawal_conflict(
    parent: Edition,
    left: WorkingDay,
    right: WorkingDay,
    left_active: set[int],
    right_active: set[int],
) -> dict[str, object] | None:
    left_rows = _original_rows(left)
    right_rows = _original_rows(right)
    for parent_row in parent.rows:
        position = parent_row.position
        parent_liters = parent_row.observation.liters
        left_row = left_rows.get(position)
        right_row = right_rows.get(position)
        if left_row is None and right_row is not None:
            corrected = right_row.observation.liters != parent_liters
            if corrected:
                return {'kind': REMOVE_CORRECT, 'refs': (('parent', position),)}
            if position in right_active:
                return {'kind': REMOVE_MOVE, 'refs': (('parent', position),)}
        elif right_row is None and left_row is not None:
            corrected = left_row.observation.liters != parent_liters
            if corrected:
                return {'kind': REMOVE_CORRECT, 'refs': (('parent', position),)}
            if position in left_active:
                return {'kind': REMOVE_MOVE, 'refs': (('parent', position),)}
    return None


def _find_cycle(nodes: Iterable[Ref], edges: dict[Ref, list[Ref]]) -> tuple[Ref, ...]:
    """Return one actual cycle of the directed graph, or an empty tuple."""
    color: dict[Ref, int] = {}
    path: list[Ref] = []
    on_path: dict[Ref, int] = {}
    for start in nodes:
        if color.get(start, 0) != 0:
            continue
        color[start] = 1
        on_path[start] = len(path)
        path.append(start)
        stack = [(start, iter(edges.get(start, ())))]
        while stack:
            node, children = stack[-1]
            descended = False
            for child in children:
                if child == node:
                    continue
                state = color.get(child, 0)
                if state == 0:
                    color[child] = 1
                    on_path[child] = len(path)
                    path.append(child)
                    stack.append((child, iter(edges.get(child, ()))))
                    descended = True
                    break
                if state == 1:
                    return tuple(path[on_path[child]:])
            if not descended:
                color[node] = 2
                stack.pop()
                del on_path[node]
                path.pop()
    return ()


def _topological(
    refs: list[Ref], edges: list[tuple[Ref, Ref]], priority: dict[Ref, tuple[int, int]],
) -> list[Ref] | None:
    indegree: dict[Ref, int] = {ref: 0 for ref in refs}
    successors: dict[Ref, list[Ref]] = {ref: [] for ref in refs}
    seen: set[tuple[Ref, Ref]] = set()
    for before, after in edges:
        if before == after or (before, after) in seen:
            continue
        seen.add((before, after))
        successors[before].append(after)
        indegree[after] += 1
    ready = [(priority[ref], ref) for ref in refs if indegree[ref] == 0]
    heapify(ready)
    ordered: list[Ref] = []
    while ready:
        _, ref = heappop(ready)
        ordered.append(ref)
        for following in successors[ref]:
            indegree[following] -= 1
            if indegree[following] == 0:
                heappush(ready, (priority[following], following))
    if len(ordered) != len(refs):
        return None
    return ordered


def _original_edges(
    left: WorkingDay, right: WorkingDay, kept: list[int],
) -> list[tuple[Ref, Ref]]:
    """Pairwise original constraints: invert a pair if a draft inverted it."""
    left_rank = _original_rank(left)
    right_rank = _original_rank(right)
    edges: list[tuple[Ref, Ref]] = []
    ordered = sorted(kept)
    for earlier_index, earlier in enumerate(ordered):
        for later in ordered[earlier_index + 1:]:
            inverted = (
                earlier in left_rank and later in left_rank
                and left_rank[earlier] > left_rank[later]
            ) or (
                earlier in right_rank and later in right_rank
                and right_rank[earlier] > right_rank[later]
            )
            if inverted:
                edges.append((('parent', later), ('parent', earlier)))
            else:
                edges.append((('parent', earlier), ('parent', later)))
    return edges


def _supplement_edges(
    draft: WorkingDay, side: str, kept: set[int],
) -> list[tuple[Ref, Ref]]:
    """A draft's supplements keep their order against its surviving rows."""
    surviving: list[Ref] = []
    for index, row in enumerate(draft.rows, 1):
        if row.parent_position is not None:
            if row.parent_position in kept:
                surviving.append(('parent', row.parent_position))
        else:
            surviving.append((side, index))
    edges: list[tuple[Ref, Ref]] = []
    for earlier in range(len(surviving)):
        for later in range(earlier + 1, len(surviving)):
            first, second = surviving[earlier], surviving[later]
            # Only relations touching a supplement belong to this rule; the
            # original-original order is governed by the inversion rule.
            if first[0] == 'parent' and second[0] == 'parent':
                continue
            edges.append((first, second))
    return edges


def _merged_rows(
    parent: Edition, left: WorkingDay, right: WorkingDay,
    left_active: set[int], right_active: set[int],
) -> tuple[tuple[DraftRow, ...] | None, dict[str, object] | None]:
    conflict = _value_conflict(parent, left, right)
    if conflict is not None:
        return None, conflict
    conflict = _withdrawal_conflict(parent, left, right, left_active, right_active)
    if conflict is not None:
        return None, conflict

    left_rows = _original_rows(left)
    right_rows = _original_rows(right)
    # A withdrawal in either draft removes the original unless it conflicted
    # above, so only originals retained by both drafts survive the merge.
    kept = sorted(left_rows.keys() & right_rows.keys())
    parent_by_position = {row.position: row for row in parent.rows}
    kept_set = set(kept)

    original_edges = _original_edges(left, right, kept)
    left_supplements = _supplement_rows(left)
    right_supplements = _supplement_rows(right)
    refs = [('parent', position) for position in kept]
    refs.extend(('left', index) for index, _row in left_supplements)
    refs.extend(('right', index) for index, _row in right_supplements)

    edges = list(original_edges)
    edges += _supplement_edges(left, 'left', kept_set)
    edges += _supplement_edges(right, 'right', kept_set)

    # Left supplements come first among genuinely unordered cross-draft pairs.
    edges += _left_preference_edges(
        edges,
        [('left', index) for index, _row in left_supplements],
        [('right', index) for index, _row in right_supplements],
    )

    priority: dict[Ref, tuple[int, int]] = {
        ('parent', position): (0, position) for position in kept
    }
    for index, _row in left_supplements:
        priority[('left', index)] = (1, index)
    for index, _row in right_supplements:
        priority[('right', index)] = (2, index)

    ordered = _topological(refs, edges, priority)
    if ordered is None:
        members = _find_cycle(refs, _adjacency(edges))
        return None, {'kind': ORDER, 'refs': members}

    supplement_by_ref = {('left', index): row for index, row in left_supplements}
    supplement_by_ref.update({('right', index): row for index, row in right_supplements})

    rows: list[DraftRow] = []
    for ref in ordered:
        if ref[0] == 'parent':
            position = ref[1]
            parent_row = parent_by_position[position]
            parent_liters = parent_row.observation.liters
            left_row = left_rows[position]
            right_row = right_rows[position]
            # Keep a correction from whichever draft changed the reading.  Two
            # corrections that landed on the same value are interchangeable.
            if left_row.observation.liters != parent_liters:
                chosen = left_row
            elif right_row.observation.liters != parent_liters:
                chosen = right_row
            else:
                chosen = left_row
            rows.append(DraftRow(chosen.observation, parent_row.origin, position))
        else:
            supplement = supplement_by_ref[ref]
            rows.append(DraftRow(supplement.observation, supplement.origin, None))
    return tuple(rows), None


def _adjacency(edges: Iterable[tuple[Ref, Ref]]) -> dict[Ref, list[Ref]]:
    graph: dict[Ref, list[Ref]] = {}
    for before, after in edges:
        if before != after:
            graph.setdefault(before, []).append(after)
    return graph


def _reaches(graph: dict[Ref, list[Ref]], start: Ref, target: Ref) -> bool:
    stack = [start]
    seen = {start}
    while stack:
        node = stack.pop()
        if node == target:
            return True
        for following in graph.get(node, ()):
            if following not in seen:
                seen.add(following)
                stack.append(following)
    return False


def _left_preference_edges(
    base_edges: list[tuple[Ref, Ref]],
    left_supplements: list[Ref],
    right_supplements: list[Ref],
) -> list[tuple[Ref, Ref]]:
    """Prefer a left supplement over a right one only when unconstrained.

    Each candidate pair is ordered left-to-right only while the combined graph
    stays acyclic.  This is a pure tie-break: it can never turn a satisfiable
    order into a conflict, because an already-related pair is skipped and any
    pair that would close a cycle is dropped.
    """
    graph = _adjacency(base_edges)
    for ref in left_supplements + right_supplements:
        graph.setdefault(ref, [])
    added: list[tuple[Ref, Ref]] = []
    for left_ref in left_supplements:
        for right_ref in right_supplements:
            if _reaches(graph, left_ref, right_ref) or _reaches(graph, right_ref, left_ref):
                continue
            graph[left_ref].append(right_ref)
            added.append((left_ref, right_ref))
    return added


def preview_merge(db: Connection, left_id: int, right_id: int) -> dict[str, object]:
    validate_record_id(left_id, 'left draft ID')
    validate_record_id(right_id, 'right draft ID')
    if left_id == right_id:
        raise ValueError('merge requires two distinct drafts')

    db.execute('BEGIN IMMEDIATE')
    with db:
        left = load_draft(db, left_id)
        right = load_draft(db, right_id)
        if left.base.day != right.base.day:
            raise ValueError('merge requires drafts of the same day')
        if left.base.parent_id is None or right.base.parent_id is None:
            raise ValueError('merge requires drafts of one fixed parent')
        if left.base.parent_id != right.base.parent_id:
            raise ValueError('merge requires drafts of the same fixed parent')
        _require_merge_capable(db, left)
        _require_merge_capable(db, right)
        parent = load_edition(db, left.base.parent_id)

        left_active = _active_relocations(left, moved_originals(db, left.draft_id))
        right_active = _active_relocations(right, moved_originals(db, right.draft_id))
        rows, conflict = _merged_rows(parent, left, right, left_active, right_active)
        if conflict is not None:
            return {'preview_id': None, 'rows': (), 'conflict': conflict}

        assert rows is not None
        preview_id = _store_preview(db, left, right, rows)
        return {'preview_id': preview_id, 'rows': rows, 'conflict': None}


def _store_preview(
    db: Connection, left: WorkingDay, right: WorkingDay, rows: tuple[DraftRow, ...],
) -> int:
    preview_id = db.execute(
        '''INSERT INTO merge_previews
           (day, parent_id, left_id, right_id, left_revision, right_revision)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (
            left.base.day, left.base.parent_id, left.draft_id, right.draft_id,
            draft_revision(db, left.draft_id), draft_revision(db, right.draft_id),
        ),
    ).lastrowid
    db.executemany(
        '''INSERT INTO merge_preview_rows
           (preview_id, position, label, liters, station, source_batch, source_position,
            parent_position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (
                preview_id, position, row.observation.label,
                encode_reading(row.observation.liters), row.origin.station,
                row.origin.batch_id, row.origin.line_no, row.parent_position,
            )
            for position, row in enumerate(rows, 1)
        ),
    )
    return preview_id
