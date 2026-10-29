# Real-world UVM corpus

The public testbenches that `docs/evidence.md` and the README's "Measured on real projects" table are
measured on, with everything needed to run the same measurement again. Each repository is fetched **at a
pinned commit** (`git init` + `git fetch --depth 1 --filter=blob:none origin <sha>`, with the sparse
patterns below), so the corpus stays the same after the upstreams move on. Every repository has a
permissive licence. Nothing in them is modified; generated files live in `generated/`, and the filelists
we wrote live in `filelists/`.

## How to run

```bash
tools/corpus/tools/fetch_corpus.sh                       # the clones, into tools/corpus/repos (git-ignored)
tools/corpus/run_baseline.sh -o /tmp/corpus-out \
    -c tools/corpus/results/2026-10-16/summary.json      # all 19 cases, compared with the published run
python tools/corpus_evidence.py /tmp/corpus-out/summary.json   # the Markdown tables
```

`run_baseline.sh` runs `stats`, `lint`, `elab`, `topology` and `graph` for every case in `cases.txt`,
one `python -m uvmls` process per command, timed with `/usr/bin/time`, four cases at a time (`-j`).
It uses the repository's `.venv` Python (or `UVMLS_PYTHON`) and `server/` (or `-s SERVER_DIR`: another
checkout's server directory, for example `git archive <sha> server | tar -x -C snap/<sha>` and then
`-s snap/<sha>/server`, so that later commits do not change what is measured). `results/<date>/summary.json`
is the run the published tables come from; `summarize.py --compare` reports any change in files, classes,
findings, errors or topology against it ("changes: none" is the expected answer for a change that does
not touch the analyses).

## Repositories

| Path under `repos/` | Upstream | Commit (date) | Licence | What is checked out |
|---|---|---|---|---|
| `opentitan` | https://github.com/lowRISC/opentitan | `8cfac43aa637fc6acdce3a290d8ffc6c82e3659c` (2026-10-02) | Apache-2.0 | sparse (cone): `hw/dv` (all DV libraries and agents), `hw/ip/{uart,i2c,aes,tlul,prim,prim_generic,edn,csrng,keymgr,keymgr_dpe,lc_ctrl,otp_ctrl,entropy_src,rv_dm}`, `hw/top_earlgrey/{rtl,ip_autogen,dv,data}`, `hw/vendor/pulp_riscv_dbg`, `hw/formal/tools/csr_assert_gen`, `util` (1,681 SV files) |
| `core-v-verif` | https://github.com/openhwgroup/core-v-verif | `083728bd8d548fd188e0ef43e0c1b038bce24002` (2026-09-14) | Solderpad 2.0 (Apache-2.0 wraparound) | sparse: `cv32e40p/{env,tb,tests/uvmt,sim,vendor_lib/imperas,vendor_lib/verilab}`, `lib/{uvm_agents,uvm_libs,uvm_components,sim_libs,cv_dv_utils}`, `mk`, `vendor_lib/imperas`, `core-v-cores` |
| `core-v-verif/core-v-cores/cv32e40p` | https://github.com/openhwgroup/cv32e40p | `fcd59685681bc2b58e5805497b0c59df14eda810` (2021-08-24) | Solderpad 0.51 | the core RTL at the hash `cv32e40p/sim/ExternalRepos.mk` pins (`CV_CORE_HASH ?= fcd5968`), where the makefiles clone it |
| `core-v-verif/cv32e40p/vendor_lib/verilab/svlib` | https://bitbucket.org/verilab/svlib | `c25509a7e54a880fe8f58f3daa2f891d6ecf6428` (2021-08-01) | Apache-2.0 (`svlib/LICENSE`) | the svlib the testbench filelist names (`SVLIB_HASH` in ExternalRepos.mk) |
| `caliptra-rtl` | https://github.com/chipsalliance/caliptra-rtl | `0f311d3021703f46bc9cb42ca88d84c07a599b9d` (2026-10-02) | Apache-2.0 | sparse (non-cone): all of `src/` except `src/integration/test_suites`, plus `config/`, `tools/scripts` (1,387 SV files; seven UVMF benches). Its README requires UVM 1.1d |
| `uvmf-full` | https://github.com/fvutils/uvmf-full | `33adda996be3adbbc7d6616cdb959210af6bc5d8` (2022-04-01) | Apache-2.0 | sparse: `uvmf_base_pkg`, `common` (the UVMF base library caliptra's `${UVMF_HOME}` points at) |
| `tvip-axi` | https://github.com/taichi-ishitani/tvip-axi | `955ca0f1be5265614859e7e187a6e93f41523361` (2026-10-01) | Apache-2.0 | whole repository (AXI VIP + sample env) |
| `tvip-axi/tue` | https://github.com/taichi-ishitani/tue | `b49856a47ed162fd0eea4509f2495465d0091e7f` (2024-06-28) | Apache-2.0 | pinned by `setup_submodules.sh` |
| `tvip-axi/tvip-common` | https://github.com/taichi-ishitani/tvip-common | `be09036727aac5be4c483536b12e7a1e3c0c8b8b` (2024-06-27) | Apache-2.0 | pinned by `setup_submodules.sh` |
| `yuu_ahb` | https://github.com/seabeam/yuu_ahb | `af96e0fce142441801ba53cc1724f61205957575` (2025-09-13) | MIT | whole repository (AHB VIP + test bench) |
| `yuu_ahb/pkg/yuu_common` | https://github.com/seabeam/yuu_common | `49867b67c043084de7734aacaedfd096b929b908` (2020-08-24) | MIT | submodule at its recorded commit |
| `yuu_ahb/pkg/yuu_amba` | https://github.com/seabeam/yuu_amba | `0415af95a2f43510f48e97e3c484700b447dd019` (2020-08-24) | MIT | submodule at its recorded commit |
| `UVM-Examples` | https://github.com/mayurkubavat/UVM-Examples | `f3db7263c7b3969db009a6bb264ec94a409d5e24` (2025-06-28) | Apache-2.0 | whole repository (six tutorial testbenches + three small examples in one tree) |
| `UVM-APB_RAL` | https://github.com/JoseIuri/UVM-APB_RAL | `3bbce8799916a1da65284831fb9395b2ddc0ee6f` (2020-06-19) | MIT | whole repository (uvm_reg tutorial) |

Left out of the GitHub search (sorted by stars): raysalemi/uvmprimer, cluelogic/uvm-tutorial-for-candy-lovers and
VerificationExcellence/UVMReference have no licence; Questa VIP (`QUESTA_MVC_HOME`, used by caliptra's soc_ifc
and top benches) is proprietary and not available.

## Generated inputs (`generated/`)

OpenTitan's UVM register models are generated at build time (FuseSoC `ralgen` generator), so they are not in
the repository. They were produced with OpenTitan's own tool (Apache-2.0) in a scratch venv holding the
packages in `tools/venv-freeze.txt` (hjson, mako, pyyaml, tabulate, semantic_version, mistletoe,
peakrdl-systemrdl):

    <venv>/bin/python repos/opentitan/util/regtool.py -s -t generated/opentitan/<ip> repos/opentitan/hw/ip/<ip>/data/<ip>.hjson

for `uart`, `i2c` and `aes`. Each writes `<ip>_ral_pkg.sv` plus a `.core` file. Not generated: the FPV CSR
assertion modules (`<ip>_csr_assert_fpv`, `csr_assert_gen` generator), which the `<ip>_bind` modules reference.

## Filelists (`filelists/`)

| File | Derived from | Notes |
|---|---|---|
| `opentitan_{uart,i2c,aes}.f` | `hw/ip/<ip>/dv/<ip>_sim.core`, target `sim`, tool `vcs`, resolved by `tools/fusesoc_flist.py` (dependencies first, `is_include_file` files as `+incdir+`, virtual cores bound to `prim_generic`) | defines and timescale from `hw/dv/tools/dvsim/common_sim_cfg.hjson` + `vcs.hjson`: `UVM UVM_NO_DEPRECATED UVM_REGEX_NO_DPI UVM_REG_{ADDR,DATA}_WIDTH=32 UVM_REG_BYTENABLE_WIDTH=4 SIMULATION DUT_HIER=tb.dut VCS`; tops `tb` + the `sim_tops` of `<ip>_sim_cfg.hjson`. `opentitan_aes.f` also has the `aes_gcm_masked` build-mode defines from `aes_base_sim_cfg.hjson` (`EN_MASKING=1 SBOX_IMPL=aes_pkg::SBoxImplDom EN_GCM=1`), added by hand after generation |
| `cv32e40p.f` | `mk/uvmt/xrun.mk` (`comp`), `mk/core/core.mk`, `mk/uvmt/uvmt.mk` | uses the project's own `.flist` files through `${DV_*}` variables, which `tools/env.sh` exports with the makefiles' values |
| `caliptra_sha512.f` | `src/sha512/uvmf_sha512/config/uvmf_sha512.vf` + `config/compile.yml` | variables bound; `${UVM_HOME}` and Questa VIP lines dropped (the bench does not use QVIP), including UVMF's `common/modules/ahb_memory_slave_module*`, which need QVIP; tops `hdl_top hvl_top` |
| `tvip_axi.f` | `sample/work/makefile` (FILE_LISTS, SOURCE_FILES) | the project's `compile.f` files through `${TUE_HOME}` etc., exported by `tools/env.sh` |
| `yuu_ahb.f` | `test/sim/Makefile` | `+define+YUU_UVM -f test/list/bench.lst` |
| `apb_ral.f` | `tb/Makefile` (`sim`) | |
| `uvm_examples_ahb2.f` | `projects/ahb2_uvm_tb/sim/ahb_inc.f` | without its `$UVM_HOME` lines |

## Layout

    repos/                   the clones above (git-ignored; tools/fetch_corpus.sh creates them)
    generated/               regtool output for OpenTitan (uart, i2c, aes)
    filelists/               the filelists we wrote (see above)
    cases.txt                the 19 cases (name|mode|dir|filelist|elab tops|topology test)
    run_baseline.sh          the measurement: stats, lint, elab, topology, graph per case
    results/2026-10-16/      summary.json of the run docs/evidence.md shows (the 0.1.0 server)
    results/latest/          where a run goes by default (git-ignored)
    tools/fetch_corpus.sh    fetch every repository at its pinned commit (idempotent)
    tools/env.sh             the projects' make variables (DV_*, TUE_HOME, ...), sourced by run_baseline.sh
    tools/fusesoc_flist.py   FuseSoC core -> filelist resolver (OpenTitan)
    tools/summarize.py       the table, and the comparison of two result directories
    tools/venv-freeze.txt    the regtool venv's packages

## Reproduction

The corpus was rebuilt from scratch on 2026-10-04 (same 14 repositories at the same commits, same sparse
checkouts, regtool output byte-for-byte the same size, same FuseSoC resolution: 233/241/296 files for
uart/i2c/aes) and gave the first run's table exactly, for all 19 cases: files, classes, lint findings,
elaboration errors and warnings, topology nodes. The only difference was which of disc_caliptra_soc_ifc's
2,551 elaboration errors are *listed* under the 1,000-diagnostic cap.
