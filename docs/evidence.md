# Measured on real projects

What ElegantUVM reports on public UVM testbenches, so you can judge it on evidence. Everything here is
reproducible: [`tools/corpus/`](../tools/corpus/README.md) holds the list of repositories at their pinned commits,
the filelists we wrote, `run_baseline.sh` and this run's `results/2026-10-16/summary.json`; the tables are
`tools/corpus_evidence.py` over that file.

**Run:** 2026-10-16, server at commit `2aa6fdf` (the launch fixes), Python 3.14.4, pyslang 12.0.0, WSL2 on a
16-core machine, two cases at a time. Each row is one `python -m uvmls` process per command (`stats`, `lint`,
`elab`, `topology`, `graph`), measured by `/usr/bin/time`; "Index" is the cold index of the whole project with the
bundled Accellera UVM 1.2, "RSS" the peak memory of that process.

## With the project's filelist

| Project | What it is |
|---|---|
| opentitan_uart / i2c / aes | lowRISC OpenTitan IP-level DV (`hw/ip/<ip>/dv`, `cip_base_*`, `dv_lib`), filelists resolved from the FuseSoC cores |
| cv32e40p | OpenHW core-v-verif, the CV32E40P UVM environment (`uvmt`, `uvme`, `uvml`) with Imperas and svlib vendor code |
| caliptra_sha512 | CHIPS Alliance Caliptra, the UVMF-generated SHA512 bench |
| tvip_axi | taichi-ishitani's AXI VIP sample environment |
| yuu_ahb, apb_ral, uvm_examples_ahb2 | smaller open AHB/APB benches and tutorials |

| Case | Files | Classes | Index | RSS | Lint findings | Elab errors / warnings | Topology | Graph nodes / edges |
|---|---|---|---|---|---|---|---|---|
| opentitan_uart | 370 | 156 | 1.76 s | 111 MB | 14 | 15 / 7 (0.36 s) | 42 nodes, depth 5, 22 connections | 202 / 338 |
| opentitan_i2c | 407 | 203 | 2.15 s | 122 MB | 19 | 49 / 9 (0.38 s) | 55 nodes, depth 5, 34 connections | 249 / 449 |
| opentitan_aes | 451 | 174 | 2.16 s | 126 MB | 13 | 45 / 9 (0.52 s) | 38 nodes, depth 5, 19 connections | 220 / 365 |
| cv32e40p | 230 | 142 | 1.48 s | 99 MB | 4 | 12 / 5 (0.35 s) | 61 nodes, depth 5, 42 connections | 176 / 391 |
| caliptra_sha512 | 229 | 65 | 0.84 s | 94 MB | 0 | 10 / 16 (0.22 s) | 14 nodes, depth 4, 11 connections | 87 / 131 |
| tvip_axi | 97 | 104 | 0.74 s | 83 MB | 4 | 2 / 0 (0.18 s) | 26 nodes, depth 4, 33 connections | 153 / 231 |
| yuu_ahb | 54 | 56 | 0.62 s | 82 MB | 1 | 37 / 0 (0.17 s) | 16 nodes, depth 4, 11 connections | 87 / 183 |
| apb_ral | 19 | 15 | 0.37 s | 77 MB | 0 | 2 / 0 (0.12 s) | 8 nodes, depth 4, 4 connections | 43 / 71 |
| uvm_examples_ahb2 | 34 | 49 | 0.43 s | 79 MB | 1 | 0 / 1 (0.15 s) | 15 nodes, depth 4, 8 connections | 69 / 193 |

Crashes, tracebacks or timeouts: none.

What the columns mean:

- **Lint findings** are slang's parse diagnostics on the project's own files plus the eleven UVM lints, after
  the filelist's defines. On these projects they are few, and most are slang being stricter than the simulators
  the projects target (`LifetimeForPrototype`, `UnknownEscapeCode`, `SolveBeforeDisallowed`, `EmptyBody`) or
  vendor macros the filelist does not define (`UnknownDirective`). The `config-db-no-set` warnings on OpenTitan,
  tvip-axi and yuu_ahb are knobs the benches set from the command line (`+uvm_set_config_*`) or from code the
  checkout does not include; the message says so, and `// uvm-lint: ignore` silences a known one. We found no
  finding on these nine benches that we would call a bug in ElegantUVM; if you do, please report it.
- **Elab errors / warnings** are slang's full elaboration of the filelist with the listed tops. Errors are
  dominated by what the checkout cannot provide (generated register models and FPV assertion modules for
  OpenTitan, the Imperas DPI model for cv32e40p, Questa VIP for Caliptra) and by slang's stricter checking
  (`ConcatWithStringInt`, `RangeOOB`); they are the same diagnostics the `elab` CLI shows, unchanged.
- **Topology** is the static `uvm_test_top` tree of the default test (the one `run_test` names, else the first):
  every component created with `type_id::create` down the chain, factory overrides applied, conditional creates
  marked. It is static, so arrays created in loops show once with their name expression, and knobs that pick a
  variant at runtime show as conditional (STATUS I20).
- **Graph** is the class graph: every class in the project with its inheritance, composition, create and override edges.

## Zero configuration (discovery mode)

The same repositories opened as a folder with no filelist: ElegantUVM finds the sources and infers include
directories itself. This is what a first-time user sees before configuring anything, so the diagnostics are
noisier: files meant for several compilation units or needing defines produce parse errors, and headers that the
sparse checkout does not contain produce `CouldNotOpenIncludeFile`.

| Case | Files | Classes | Index | RSS | Lint findings | Elab errors / warnings | Topology | Graph nodes / edges |
|---|---|---|---|---|---|---|---|---|
| disc_opentitan_uart_dv | 29 | 23 | 0.39 s | 78 MB | 150 | 467 / 0 (0.28 s) | — | 37 / 30 |
| disc_opentitan_hw | 1677 | 857 | 9.33 s | 314 MB | 946 | 3293 / 86 (3.93 s) | 38 nodes, depth 5, 19 connections | 906 / 1463 |
| disc_cv32e40p | 591 | 402 | 4.38 s | 139 MB | 513 | skipped | 75 nodes, depth 6, 57 connections | 454 / 1177 |
| disc_caliptra_sha512 | 74 | 68 | 0.56 s | 83 MB | 141 | 462 / 16 (0.16 s) | — | 92 / 179 |
| disc_caliptra_soc_ifc | 385 | 510 | 2.66 s | 146 MB | 1755 | 2551 / 171 (0.56 s) | — | 577 / 1379 |
| disc_caliptra_src | 1410 | 1213 | 8.46 s | 396 MB | 4055 | 8136 / 339 (2.16 s) | — | 1294 / 3468 |
| disc_tvip_axi | 148 | 133 | 0.88 s | 87 MB | 50 | 28 / 0 (0.20 s) | 26 nodes, depth 4, 33 connections | 173 / 282 |
| disc_yuu_ahb | 64 | 62 | 0.68 s | 83 MB | 1 | 74 / 0 (0.17 s) | 16 nodes, depth 4, 11 connections | 94 / 200 |
| disc_uvm_examples | 160 | 154 | 0.74 s | 83 MB | 3 | 71 / 1 (0.18 s) | 13 nodes, depth 4, 3 connections | 185 / 511 |
| disc_apb_ral | 21 | 17 | 0.35 s | 77 MB | 0 | 6 / 0 (0.13 s) | 8 nodes, depth 4, 4 connections | 45 / 74 |

`disc_cv32e40p` skips elaboration because the tree defines some packages more than once (STATUS I23).

## Lint findings by code

| Case | Lint findings by code |
|---|---|
| opentitan_uart | UnknownDirective 5, config-db-no-set 3, UnknownEscapeCode 3, LifetimeForPrototype 3 |
| opentitan_i2c | UnknownDirective 8, UnknownEscapeCode 5, config-db-no-set 3, LifetimeForPrototype 3 |
| opentitan_aes | UnknownDirective 5, UnknownEscapeCode 3, LifetimeForPrototype 3, EmptyBody 2 |
| cv32e40p | SolveBeforeDisallowed 2, EmptyBody 2 |
| caliptra_sha512 | — |
| tvip_axi | config-db-no-set 4 |
| yuu_ahb | config-db-no-set 1 |
| apb_ral | — |
| uvm_examples_ahb2 | EmptyBody 1 |
| disc_opentitan_uart_dv | UnknownDirective 135, unknown-base 7, ExpectedStatement 3, CouldNotOpenIncludeFile 3, ExpectedExpression 2 |
| disc_opentitan_hw | UnknownDirective 834, ExpectedExpression 73, ExpectedIdentifier 13, UnknownEscapeCode 11, ExpectedStatement 5, LifetimeForPrototype 3, CouldNotOpenIncludeFile 3, EmptyBody 2, unknown-base 1, ExpectedIncludeFileName 1 |
| disc_cv32e40p | UnknownDirective 254, ExpectedDeclarator 185, unknown-base 15, ExpectedToken 14, CouldNotOpenIncludeFile 10, ExpectedIdentifier 6, TypoKeyword 4, EndNameMismatch 3, config-db-no-set 3, NotAllowedInCU 3, ExpectedMember 2, UnexpectedEndDelim 2, UnknownEscapeCode 2, SolveBeforeDisallowed 2, factory-ctor 2, EmptyBody 2, InvalidSuperNew 1, InvalidGenvarIterExpression 1, BareAssociativePattern 1, phase-no-super 1 |
| disc_caliptra_sha512 | UnknownDirective 110, unknown-base 17, CouldNotOpenIncludeFile 8, ExpectedExpression 6 |
| disc_caliptra_soc_ifc | UnknownDirective 931, ExpectedExpression 617, factory-ctor 107, unknown-base 52, CouldNotOpenIncludeFile 33, BareAssociativePattern 13, EmptyBody 2 |
| disc_caliptra_src | UnknownDirective 2000, ExpectedIdentifier 1210, ExpectedExpression 341, ExpectedToken 182, unknown-base 159, factory-ctor 107, CouldNotOpenIncludeFile 27, BareAssociativePattern 13, DigitsLeadingUnderscore 9, NonstandardHierarchicalCross 3, MisplacedTrailingSeparator 2, EmptyBody 2 |
| disc_tvip_axi | UnknownDirective 45, config-db-no-set 4, ExpectedExpression 1 |
| disc_yuu_ahb | config-db-no-set 1 |
| disc_uvm_examples | EmptyBody 1, ExpectedStatement 1, ExpectedToken 1 |
| disc_apb_ral | — |

## Against the previous run

The run before the launch fixes (server at `2bbf7c9`, 2026-10-09) differs in exactly two ways, both intended:

- one `unknown-base` finding fewer on cv32e40p (svlib's `class ... #(type T) extends T`; STATUS I19);
- the topologies that stopped at the test now expand, because creates through a base test's type parameter
  resolve: opentitan_uart 1 → 42 nodes, opentitan_i2c 1 → 55, opentitan_aes 1 → 38, caliptra_sha512 1 → 14,
  tvip_axi 4 → 26, disc_opentitan_hw 1 → 38 (STATUS I21). Every other number, including every lint message,
  is identical.

## Limits of this evidence

The corpus is nine filelist benches and ten discovery folders from seven public projects; it exercises UVM
testbench structure well and register models, assertions and DPI poorly (the checkouts lack the generated and
vendor parts). "No finding we would call a bug" is our reading of the reports, not a proof. The index is
syntax-level by design (README, "Why syntax-level indexing"), so name resolution has known gaps listed in
`docs/STATUS.md` (I01–I08).
