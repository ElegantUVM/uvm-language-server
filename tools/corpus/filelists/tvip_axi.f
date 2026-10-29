// tvip-axi sample environment, as sample/work/makefile builds it (TUE_HOME, TVIP_COMMON_HOME = submodules
// fetched by setup_submodules.sh at their pinned hashes).
-timescale 1ns/1ps
-f ../repos/tvip-axi/tue/compile.f
-f ../repos/tvip-axi/tvip-common/compile.f
-f ../repos/tvip-axi/compile.f
-f ../repos/tvip-axi/sample/env/compile.f
../repos/tvip-axi/sample/env/tvip_axi_sample_delay.sv
../repos/tvip-axi/sample/env/top.sv
