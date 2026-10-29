#!/usr/bin/env bash
# Fetch every corpus repository at its pinned commit (see MANIFEST.md), with shallow, blob-filtered
# fetches and the same sparse checkouts the original baseline used. Re-runnable: a repository that is
# already at its commit is left alone.
#
#   tools/fetch_corpus.sh            # all
#   tools/fetch_corpus.sh opentitan  # one (by directory under repos/)
set -u
CORPUS=$(cd "$(dirname "$0")/.." && pwd)
R=$CORPUS/repos
mkdir -p "$R" "$CORPUS/logs"
ONLY=" $* "

want() { [[ $ONLY == "  " || $ONLY == *" $1 "* ]]; }

# fetch DIR URL SHA [cone|nocone PATTERN...]
fetch() {
  local dir=$1 url=$2 sha=$3; shift 3
  local mode=${1:-full}; [ $# -gt 0 ] && shift
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$sha" ]; then
    echo "ok   $dir $sha"; return 0
  fi
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$dir" init -q && git -C "$dir" remote add origin "$url" || return 1
  case $mode in
    cone)   git -C "$dir" sparse-checkout set "$@" ;;
    nocone) git -C "$dir" sparse-checkout set --no-cone "$@" ;;
  esac
  local filt=(--filter=blob:none); [ "$mode" = full ] && filt=()
  if ! git -C "$dir" fetch -q --depth 1 "${filt[@]}" origin "$sha" 2>"$CORPUS/logs/fetch_$(basename "$dir").err"; then
    echo "FAIL $dir: cannot fetch $sha from $url (see logs/fetch_$(basename "$dir").err)"; return 1
  fi
  git -C "$dir" checkout -q FETCH_HEAD && echo "got  $dir $(git -C "$dir" rev-parse HEAD)"
}

if want opentitan; then
  fetch "$R/opentitan" https://github.com/lowRISC/opentitan.git 8cfac43aa637fc6acdce3a290d8ffc6c82e3659c cone \
    hw/dv hw/ip/uart hw/ip/i2c hw/ip/aes hw/ip/tlul hw/ip/prim hw/ip/prim_generic hw/ip/edn hw/ip/csrng \
    hw/ip/keymgr hw/ip/lc_ctrl hw/ip/otp_ctrl hw/ip/entropy_src hw/top_earlgrey/rtl hw/top_earlgrey/ip_autogen \
    hw/top_earlgrey/dv hw/top_earlgrey/data util hw/ip/rv_dm hw/vendor/pulp_riscv_dbg \
    hw/formal/tools/csr_assert_gen hw/ip/keymgr_dpe
fi
if want core-v-verif; then
  fetch "$R/core-v-verif" https://github.com/openhwgroup/core-v-verif.git 083728bd8d548fd188e0ef43e0c1b038bce24002 nocone \
    '/*' '!/*/' '/cv32e40p/env/' '/cv32e40p/tb/' '/cv32e40p/tests/uvmt/' '/cv32e40p/sim/' \
    '/cv32e40p/vendor_lib/imperas/' '/cv32e40p/vendor_lib/verilab/' '/lib/uvm_agents/' '/lib/uvm_libs/' \
    '/lib/uvm_components/' '/lib/sim_libs/' '/lib/cv_dv_utils/' '/mk/' '/vendor_lib/imperas/' '/core-v-cores/'
  # The core RTL at the hash cv32e40p/sim/ExternalRepos.mk pins (CV_CORE_HASH ?= fcd5968), where the makefiles clone it.
  fetch "$R/core-v-verif/core-v-cores/cv32e40p" https://github.com/openhwgroup/cv32e40p.git \
    fcd59685681bc2b58e5805497b0c59df14eda810 nocone '/*'
  # svlib (SVLIB_HASH in ExternalRepos.mk). Bitbucket: clone, then check the commit.
  sv=$R/core-v-verif/cv32e40p/vendor_lib/verilab/svlib
  if [ "$(git -C "$sv" rev-parse HEAD 2>/dev/null)" != c25509a7e54a880fe8f58f3daa2f891d6ecf6428 ]; then
    rm -rf "$sv"
    timeout 120 git clone -q https://bitbucket.org/verilab/svlib.git "$sv" 2>"$CORPUS/logs/fetch_svlib.err" &&
      git -C "$sv" checkout -q c25509a7e54a880fe8f58f3daa2f891d6ecf6428 && echo "got  $sv $(git -C "$sv" rev-parse HEAD)" ||
      echo "FAIL $sv (see logs/fetch_svlib.err)"
  else
    echo "ok   $sv"
  fi
fi
if want caliptra-rtl; then
  fetch "$R/caliptra-rtl" https://github.com/chipsalliance/caliptra-rtl.git 0f311d3021703f46bc9cb42ca88d84c07a599b9d nocone \
    '/*' '!/*/' '/src/' '!/src/integration/test_suites/' '/config/' '/tools/scripts/'
fi
if want uvmf-full; then
  fetch "$R/uvmf-full" https://github.com/fvutils/uvmf-full.git 33adda996be3adbbc7d6616cdb959210af6bc5d8 cone \
    uvmf_base_pkg common
fi
if want tvip-axi; then
  fetch "$R/tvip-axi" https://github.com/taichi-ishitani/tvip-axi.git 955ca0f1be5265614859e7e187a6e93f41523361
  # setup_submodules.sh's pinned hashes
  fetch "$R/tvip-axi/tue" https://github.com/taichi-ishitani/tue.git b49856a47ed162fd0eea4509f2495465d0091e7f
  fetch "$R/tvip-axi/tvip-common" https://github.com/taichi-ishitani/tvip-common.git be09036727aac5be4c483536b12e7a1e3c0c8b8b
fi
if want yuu_ahb; then
  fetch "$R/yuu_ahb" https://github.com/seabeam/yuu_ahb.git af96e0fce142441801ba53cc1724f61205957575
  # submodules at their recorded commits
  fetch "$R/yuu_ahb/pkg/yuu_amba" https://github.com/seabeam/yuu_amba.git 0415af95a2f43510f48e97e3c484700b447dd019
  fetch "$R/yuu_ahb/pkg/yuu_common" https://github.com/seabeam/yuu_common.git 49867b67c043084de7734aacaedfd096b929b908
fi
if want UVM-Examples; then
  fetch "$R/UVM-Examples" https://github.com/mayurkubavat/UVM-Examples.git f3db7263c7b3969db009a6bb264ec94a409d5e24
fi
if want UVM-APB_RAL; then
  fetch "$R/UVM-APB_RAL" https://github.com/JoseIuri/UVM-APB_RAL.git 3bbce8799916a1da65284831fb9395b2ddc0ee6f
fi
