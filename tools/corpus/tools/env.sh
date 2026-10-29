# Sourced with CORPUS set: what the projects' own makefiles export, so that the filelists' ${VAR}s resolve.
# (core-v-verif mk/core/core.mk + mk/uvmt/uvmt.mk, tvip-axi sample/work/makefile.)
unset UVM_HOME  # every case uses the bundled UVM 1.2 unless its filelist brings one
export CORE_V_VERIF=$CORPUS/repos/core-v-verif CV_CORE_LC=cv32e40p
export DV_UVMT_PATH=$CORE_V_VERIF/cv32e40p/tb/uvmt DV_UVME_PATH=$CORE_V_VERIF/cv32e40p/env/uvme
export TBSRC_HOME=$CORE_V_VERIF/cv32e40p/tb DESIGN_RTL_DIR=$CORE_V_VERIF/core-v-cores/cv32e40p/rtl
for v in hrtbt trn logs sb mem; do export DV_UVML_${v^^}_PATH=$CORE_V_VERIF/lib/uvm_libs/uvml_$v; done
for v in core_cntrl isacov rvfi rvvi rvvi_ovpsim clknrst interrupt debug pma obi_memory fencei; do
  export DV_UVMA_${v^^}_PATH=$CORE_V_VERIF/lib/uvm_agents/uvma_$v
done
export DV_UVMC_RVFI_SCOREBOARD_PATH=$CORE_V_VERIF/lib/uvm_components/uvmc_rvfi_scoreboard
export DV_UVMC_RVFI_REFERENCE_MODEL_PATH=$CORE_V_VERIF/lib/uvm_components/uvmc_rvfi_reference_model
export DV_OVPM_HOME=$CORE_V_VERIF/vendor_lib/imperas DV_OVPM_MODEL=$CORE_V_VERIF/vendor_lib/imperas/imperas_DV_COREV
export DV_OVPM_DESIGN=$CORE_V_VERIF/vendor_lib/imperas/design DV_SVLIB_PATH=$CORE_V_VERIF/cv32e40p/vendor_lib/verilab
export TVIP_AXI_HOME=$CORPUS/repos/tvip-axi TUE_HOME=$CORPUS/repos/tvip-axi/tue
export TVIP_COMMON_HOME=$CORPUS/repos/tvip-axi/tvip-common
