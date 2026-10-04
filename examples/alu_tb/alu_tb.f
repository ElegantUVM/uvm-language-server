// Filelist in the same form a simulator takes (VCS / Xcelium / Questa).
// Add +incdir+$UVM_HOME/src and $UVM_HOME/src/uvm_pkg.sv here to index the real UVM
// library; without them the language server falls back to its built-in UVM model.
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
