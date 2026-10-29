"""Versioned, read-only content presentation; no selection or measurement."""
from copy import deepcopy

VERSION = 'desktop-pet-single-content-text-v1'
CURRENT_VERSION = 'desktop-pet-observation-and-definition-v2'
# The v2 experiment did not resolve the saved real Writer failure (QUALITY147).
# Keep it explicitly reproducible; v3 preserves v1 text and hides backend metadata.
CLEAN_VERSION = 'desktop-pet-clean-measurement-view-v3'
PUBLIC_VERSION = 'desktop-pet-public-measurement-view-v4'
DEFAULT_VERSION = PUBLIC_VERSION
HIERARCHY_MEMORY_VERSION = 'desktop-pet-hierarchical-memory-text-v6'
DECLARED_MEMORY_VERSION = 'desktop-pet-declared-memory-text-v5'
RELATION_MEMORY_VERSION = 'desktop-pet-source-relation-text-v7'
QUERY_MEMORY_VERSION = 'desktop-pet-source-query-text-v8'


def content_view(contents, attributes, *, version=None):
    if version in (DECLARED_MEMORY_VERSION,HIERARCHY_MEMORY_VERSION,RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION):version=PUBLIC_VERSION
    if version not in (None, VERSION, CURRENT_VERSION, CLEAN_VERSION, PUBLIC_VERSION):
        raise ValueError('unsupported pet content view')
    if len(contents) != len(attributes):
        raise ValueError('content attributes differ from visible contents')
    rows = deepcopy(attributes)
    text = []
    for content, row in zip(contents, rows):
        if '交互摘要' in row:
            text.append('[用户交互] ' + row['交互摘要'])
            if version in (VERSION, CURRENT_VERSION, CLEAN_VERSION, PUBLIC_VERSION):
                del row['交互摘要']
            if version == CURRENT_VERSION:
                from .interaction_delivery import project_parameters
                parameters = project_parameters(content.parameters)
                row['片段末接触状态'] = {'started':'刚按下','ongoing':'仍按住','ended':'已松开'}[parameters['phase']]
                if '操作含义' in row:
                    row['包内通用操作定义'] = row.pop('操作含义')
                if '起始部位' in row:
                    row['起始命中区域'] = row.pop('起始部位')
        else:
            text.append(content.as_text())
    return '\n'.join(text), rows
