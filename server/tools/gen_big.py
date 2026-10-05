"""Generate the deterministic 2,000-class quick-fix benchmark (no external dependencies)."""

import argparse
from pathlib import Path


def generate(root: Path, classes: int = 2000) -> Path:
    if classes < 21:
        raise ValueError('the benchmark needs at least 21 classes')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'classes').mkdir(exist_ok=True)
    (root / 'inc' / 'sub').mkdir(parents=True, exist_ok=True)
    (root / 'inc' / 'sub' / 'hdr.svh').write_text('// Include candidate.\n', encoding='utf-8')
    files = []
    for i in range(classes - 1):
        name = f'node_{i:04d}'
        path = root / 'classes' / (name + '.sv')
        path.write_text(f'''import uvm_pkg::*;
class {name} extends uvm_component;
  `uvm_component_utils({name})
  int value;
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
  function void setup();
    uvm_config_db #(int)::set(this, "*", "cfg_{i:04d}", 1);
    void'(uvm_config_db #(int)::get(this, "", "cfg_{i:04d}", value));
  endfunction
endclass
''', encoding='utf-8')
        files.append(path.relative_to(root).as_posix())
    members = '\n'.join(f'  node_{i:04d} child{i};' for i in range(20))
    calls = '\n'.join(f'    child{i} = node_{i:04d}::type_id::create("child{i}");' for i in range(20))
    gets = '\n'.join(f'    void\'(uvm_config_db #(int)::get(this, "", "Cfg_{i:04d}", value));' for i in range(20))
    (root / 'bench.sv').write_text(f'''`include "sub/hdr.svh"
import uvm_pkg::*;
class bench extends uvm_env;
  `uvm_component_utils(bench)
  int value;
{members}
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
{calls}
{gets}
  endfunction
endclass
''', encoding='utf-8')
    filelist = root / 'big.f'
    filelist.write_text('\n'.join(files + ['bench.sv']) + '\n', encoding='utf-8')
    return filelist


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--classes', type=int, default=2000)
    args = parser.parse_args()
    print(generate(args.directory, args.classes))


if __name__ == '__main__':
    main()
