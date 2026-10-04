// Filelist in the same form a simulator takes (VCS / Xcelium / Questa).
// UVM is not listed, so the language server indexes the Accellera UVM 1.2 it ships with.
// To use another UVM, add +incdir+$UVM_HOME/src and $UVM_HOME/src/uvm_pkg.sv here.
+incdir+agent
+incdir+env
+incdir+seq
+incdir+tests
rtl/alu.sv
agent/alu_if.sv
agent/alu_pkg.sv
env/alu_env_pkg.sv
seq/alu_seq_pkg.sv
tests/alu_test_pkg.sv
tb/tb_top.sv
