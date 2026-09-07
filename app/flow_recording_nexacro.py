"""Conservative locator adaptation for recordings of Nexacro applications.

The recorder captures DOM interactions. Recycled grid cells are not record
identities. They must be repaired or recorded through a stable report route;
they are never converted into guessed dataset indexes.
"""
from __future__ import annotations

import copy
import re

from app.flow_recording import walk_steps

VOLATILE_ROW = re.compile(r'gridrow_|gridcell_|\brow_\d|:nth-|\.nth\(', re.I)
FAVORITE_GRID = re.compile(r'grd_bookmark|div_favorite', re.I)
FAVORITE_REPAIR = ('This click is on a recycled GSCM Favorite list row. Choose “Bookmark in GSCM Favorite list” '
                   'beside this step and enter the exact bookmark name.')


def favorite_grid_click(step):
    """True when a recorded click's component path identifies the Favorite grid."""
    return step.get('action') == 'click' and any(
        part.get('method') == 'locator' and any(isinstance(arg, str) and FAVORITE_GRID.search(arg) for arg in part.get('args', []))
        for part in step.get('locator', []))


def recorded_text(step):
    """The exact text the recorder captured for this click, if it captured any."""
    for part in reversed(step.get('locator', [])):
        if part.get('method') == 'get_by_text' and part.get('args') and isinstance(part['args'][0], str):
            value = part['args'][0].strip()
            return value if 0 < len(value) <= 300 else ''
    return ''


def adapt_recording(definition):
    definition = copy.deepcopy(definition)
    for step in walk_steps(definition['steps']):
        if favorite_grid_click(step) and step.get('bookmark_target') is None:
            # The recorder cannot know a grid virtualizes. Its component path
            # identifies the Favorite grid, so suggest the bookmark target and
            # reuse the recorded exact text when there is one; a recycled row
            # ID alone asks the user for the name beside this step.
            text = recorded_text(step)
            if text:
                step['bookmark_target'] = {'kind': 'gscm_favorite', 'bookmark_name': text}
                step['locator_note'] = 'Selected by exact bookmark name in the GSCM Favorite list.'
                continue
            step['repair_reason'] = FAVORITE_REPAIR
            continue
        for part in step.get('locator', []):
            if part['method'] != 'locator' or not part.get('args') or not isinstance(part['args'][0], str):
                continue
            selector = part['args'][0]
            if VOLATILE_ROW.search(selector):
                step['repair_reason'] = 'This GSCM grid cell is recycled. Choose a stable text/label locator or use the bookmark method.'
                continue
            # Codegen escapes dots in Nexacro component IDs. Ignore only the
            # instance-numbered frame prefix; keep the full component suffix.
            if selector.startswith('#') and not re.search(r'(?<!\\)[ >,+~\[\]:]', selector):
                identifier = selector[1:].replace('\\.', '.')
                match = re.search(r'(?:^|\.)(?:Setting|WorkFrame)\d+\.(.+)$', identifier)
                if match and re.fullmatch(r'[A-Za-z0-9_.-]+', match[1]):
                    part['args'][0] = '[id$=".' + match[1] + '"]:visible'
                    step['locator_note'] = 'Uses the complete Nexacro component suffix; execution requires one matching visible control.'
    return definition


def validate_target(target):
    for part in target.get('locator', []):
        if part.get('method') == 'locator' and any(VOLATILE_ROW.search(str(arg)) for arg in part.get('args', [])):
            raise ValueError('GSCM virtual grid row/cell IDs cannot identify a recorded action. Repair the locator or use the bookmark method.')
