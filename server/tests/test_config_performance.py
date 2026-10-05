"""config_db analysis is shared by lint and fixes, with bounded candidate lookup."""

import random
import string
import time
from unittest.mock import patch

import pytest

from test_fixes import index, offered
from uvmls import lints, views


@pytest.mark.parametrize("field", ["Checks_Enable", "checks_enabled"])
def test_config_analysis_is_reused_until_the_index_changes(tmp_path, field):
    text = '''import uvm_pkg::*;
class env extends uvm_env;
  int v;
  function void setup();
    uvm_config_db #(int)::set(this, "*", "Checks_Enable", 1);
    GETS
  endfunction
endclass
'''.replace('GETS', '\n'.join('void\'(uvm_config_db #(int)::get(this, "", "checks_enable", v));' for _ in range(5)))
    text = text.replace("Checks_Enable", field)
    ws = index(tmp_path, {'t.sv': text})
    with patch.object(views, 'canonical_type', wraps=views.canonical_type) as canonical, \
            patch.object(lints, 'osa_distance', wraps=lints.osa_distance) as distance:
        published = lints.lint(ws)
        assert distance.call_count == (0 if field == "Checks_Enable" else 1)
        report = views.config_db_report(ws)
        count, comparisons = canonical.call_count, distance.call_count
        ds = [d for d in published[str(tmp_path / 't.sv')] if d.code == 'config-db-no-set']
        assert len(ds) == 5
        for d in ds:
            [fix] = offered(ws, d)
            assert fix.changes[0].text == field
        assert canonical.call_count == count
        assert distance.call_count == comparisons
        assert views.config_db_report(ws) is report
        ws.update_document(str(tmp_path / 't.sv'), text.replace('"' + field + '"', '"checks_enable"'))
        assert not any(d.code == 'config-db-no-set' for ds in lints.lint(ws).values() for d in ds)
        assert views.config_db_report(ws) is not report
        assert canonical.call_count > count


def brute_near_misses(report, get):
    name, kind = get['field'], get['canon'] or get['type']
    if len(name) < 4:
        return []
    key, limit = name.lower().replace('_', ''), 1 if len(name) < 8 else 2
    out = []
    for row in report['fields']:
        other = row['field']
        if other == name or len(other) < 4 or not row['sets']:
            continue
        normal = other.lower().replace('_', '') == key
        if not normal and (abs(len(other) - len(name)) > limit or lints.osa_distance(name, other) > limit):
            continue
        match = next((s for s in row['sets'] if (s['canon'] or s['type']) == kind), None)
        read = any((g['canon'] or g['type']) == kind for g in row['gets'])
        if match and (not read or normal or lints._slip(name, other)):
            out.append((other, match))
    return out


@pytest.mark.parametrize('seed', range(4))
def test_near_miss_index_preserves_distance_type_and_order(seed):
    rng = random.Random(seed)
    names = ['Checks_Enable', 'checks_enable', 'acbd', 'abcdefgh', 'abcdfegh']
    names += [''.join(rng.choices('ab_cA', k=rng.randrange(3, 11))) for _ in range(60)]
    fields = []
    for name in dict.fromkeys(names):
        sets = [{'field': name, 'canon': kind, 'type': kind} for kind in ('int', 'bit') if rng.random() < .8]
        fields.append({'field': name, 'sets': sets, 'gets': sets[:1] if rng.random() < .3 else []})
    report = {'fields': fields}
    queries = ['checks__enable', 'abcd', 'abcedfgh', 'abcdefgh'] + names[:20]
    for name in queries:
        for kind in ('int', 'bit'):
            get = {'field': name, 'canon': kind, 'type': kind}
            assert lints.near_misses(report, get) == brute_near_misses(report, get)


def test_near_miss_lookup_scales_to_thousands_of_fields(tmp_path):
    rng = random.Random(27)
    names = list(dict.fromkeys(''.join(rng.choices(string.ascii_lowercase, k=10)) for _ in range(3300)))
    calls = [f'uvm_config_db #(int)::set(this, "*", "{name}", 1);' for name in names[:3000]]
    calls += [f'void\'(uvm_config_db #(int)::get(this, "", "{name}", v));' for name in names[3000:]]
    text = 'import uvm_pkg::*; class env extends uvm_env; int v; function void setup();\n' + \
        '\n'.join(calls) + '\nendfunction endclass\n'
    ws = index(tmp_path, {'t.sv': text})
    report = views.config_db_report(ws)
    gets = [g for row in report['fields'] for g in row['gets']]
    assert len(gets) == 300
    start = time.perf_counter()
    for get in gets:
        assert lints.near_misses(report, get) == []
    assert time.perf_counter() - start < 2.0
