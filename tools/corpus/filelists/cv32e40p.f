// core-v-verif cv32e40p UVM testbench, as mk/uvmt/xrun.mk (comp target) compiles it.
// The ${DV_*} / ${TBSRC_HOME} / ${DESIGN_RTL_DIR} variables are exported by run_baseline.sh with the
// values mk/core/core.mk and mk/uvmt/uvmt.mk give them (CORE_V_VERIF=repos/core-v-verif, CV_CORE=cv32e40p).
-timescale 1ns/1ps
+define+CV32E40P_ASSERT_ON
+define+CV32E40P_TRACE_EXECUTION
+define+UVM
+incdir+${DV_UVME_PATH}
+incdir+${DV_UVMT_PATH}
${DV_UVMT_PATH}/uvmt_cv32e40p_uvm_macros_inc.sv
-f ${DESIGN_RTL_DIR}/../cv32e40p_manifest.flist
-f ${DV_UVMT_PATH}/uvmt_cv32e40p.flist
-f ${DV_UVMT_PATH}/imperas_iss.flist
