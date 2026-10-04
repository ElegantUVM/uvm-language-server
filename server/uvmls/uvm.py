"""Built-in knowledge of the UVM base class library.

Used to classify user classes (driver, monitor, agent, ...) and to fill in the
UVM part of the class graph when the real UVM sources are not indexed. When they
are indexed, the real declarations take precedence and this table only supplies
roles.
"""

from __future__ import annotations

import re

# class name -> (base class, type parameters)
BUILTIN_CLASSES: dict[str, tuple[str | None, str]] = {
    "uvm_void": (None, ""),
    "uvm_object": ("uvm_void", ""),
    "uvm_transaction": ("uvm_object", ""),
    "uvm_sequence_item": ("uvm_transaction", ""),
    "uvm_sequence_base": ("uvm_sequence_item", ""),
    "uvm_sequence": ("uvm_sequence_base", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_sequence_library": ("uvm_sequence", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_reg_sequence": ("uvm_sequence", "#(type BASE = uvm_sequence #(uvm_reg_item))"),
    "uvm_reg_frontdoor": ("uvm_reg_sequence", ""),
    "uvm_reg_item": ("uvm_sequence_item", ""),
    "uvm_report_object": ("uvm_object", ""),
    "uvm_component": ("uvm_report_object", ""),
    "uvm_root": ("uvm_component", ""),
    "uvm_test": ("uvm_component", ""),
    "uvm_env": ("uvm_component", ""),
    "uvm_agent": ("uvm_component", ""),
    "uvm_driver": ("uvm_component", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_push_driver": ("uvm_component", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_monitor": ("uvm_component", ""),
    "uvm_scoreboard": ("uvm_component", ""),
    "uvm_subscriber": ("uvm_component", "#(type T = int)"),
    "uvm_sequencer_base": ("uvm_component", ""),
    "uvm_sequencer_param_base": ("uvm_sequencer_base", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_sequencer": ("uvm_sequencer_param_base", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_push_sequencer": ("uvm_sequencer_param_base", "#(type REQ = uvm_sequence_item, type RSP = REQ)"),
    "uvm_reg_predictor": ("uvm_component", "#(type BUSTYPE = int)"),
    "uvm_reg_adapter": ("uvm_object", ""),
    "uvm_reg_block": ("uvm_object", ""),
    "uvm_reg_file": ("uvm_object", ""),
    "uvm_reg": ("uvm_object", ""),
    "uvm_reg_field": ("uvm_object", ""),
    "uvm_mem": ("uvm_object", ""),
    "uvm_vreg": ("uvm_object", ""),
    "uvm_reg_map": ("uvm_object", ""),
    "uvm_callback": ("uvm_object", ""),
    "uvm_report_catcher": ("uvm_callback", ""),
    "uvm_report_server": ("uvm_object", ""),
    "uvm_event": ("uvm_object", ""),
    "uvm_barrier": ("uvm_object", ""),
    "uvm_tlm_fifo_base": ("uvm_component", "#(type T = int)"),
    "uvm_tlm_fifo": ("uvm_tlm_fifo_base", "#(type T = int)"),
    "uvm_tlm_analysis_fifo": ("uvm_tlm_fifo", "#(type T = int)"),
    "uvm_in_order_comparator": ("uvm_component", "#(type T = int)"),
    "uvm_algorithmic_comparator": ("uvm_component", ""),
    "uvm_port_base": ("uvm_object", "#(type IF = uvm_void)"),
    "uvm_analysis_port": ("uvm_port_base", "#(type T = int)"),
    "uvm_analysis_export": ("uvm_port_base", "#(type T = int)"),
    "uvm_analysis_imp": ("uvm_port_base", "#(type T = int, type IMP = int)"),
    "uvm_seq_item_pull_port": ("uvm_port_base", "#(type REQ = int, type RSP = REQ)"),
    "uvm_seq_item_pull_export": ("uvm_port_base", "#(type REQ = int, type RSP = REQ)"),
    "uvm_seq_item_pull_imp": ("uvm_port_base", "#(type REQ = int, type RSP = REQ, type IMP = int)"),
    "uvm_phase": ("uvm_object", ""),
    "uvm_factory": ("uvm_object", ""),
    "uvm_config_db": (None, "#(type T = int)"),
    "uvm_resource_db": (None, "#(type T = uvm_object)"),
    "uvm_object_registry": (None, "#(type T = uvm_object, string Tname = \"<unknown>\")"),
    "uvm_component_registry": (None, "#(type T = uvm_component, string Tname = \"<unknown>\")"),
}

# The most specific UVM base wins, so this is checked while walking up a chain.
ROLE_OF_BASE: dict[str, str] = {
    "uvm_test": "test",
    "uvm_env": "env",
    "uvm_agent": "agent",
    "uvm_driver": "driver",
    "uvm_push_driver": "driver",
    "uvm_monitor": "monitor",
    "uvm_sequencer": "sequencer",
    "uvm_push_sequencer": "sequencer",
    "uvm_sequencer_param_base": "sequencer",
    "uvm_sequencer_base": "sequencer",
    "uvm_scoreboard": "scoreboard",
    "uvm_subscriber": "subscriber",
    "uvm_in_order_comparator": "scoreboard",
    "uvm_algorithmic_comparator": "scoreboard",
    "uvm_reg_predictor": "predictor",
    "uvm_reg_adapter": "reg_adapter",
    "uvm_reg_block": "reg_model",
    "uvm_reg_file": "reg_model",
    "uvm_reg": "reg_model",
    "uvm_reg_field": "reg_model",
    "uvm_mem": "reg_model",
    "uvm_vreg": "reg_model",
    "uvm_reg_map": "reg_model",
    "uvm_reg_sequence": "sequence",
    "uvm_reg_frontdoor": "sequence",
    "uvm_sequence_library": "sequence",
    "uvm_sequence": "sequence",
    "uvm_sequence_base": "sequence",
    "uvm_sequence_item": "item",
    "uvm_callback": "callback",
    "uvm_report_catcher": "callback",
    "uvm_tlm_fifo_base": "tlm",
    "uvm_port_base": "port",
    "uvm_component": "component",
    "uvm_object": "object",
}

COMPONENT_ROLES = {
    "test", "env", "agent", "driver", "monitor", "sequencer", "scoreboard",
    "subscriber", "predictor", "tlm", "component",
}

TLM_PORT_RE = re.compile(
    r"^uvm_(analysis|seq_item_pull|blocking|nonblocking|get_peek|blocking_get_peek|"
    r"nonblocking_get_peek|put|get|peek|transport|master|slave|tlm_b|tlm_nb)\w*_(port|export|imp|socket)\w*$"
    r"|^uvm_analysis_imp\w*$|^uvm_\w+_imp_\w+$"
)

ROLE_ORDER = [
    "test", "env", "agent", "driver", "monitor", "sequencer", "scoreboard",
    "subscriber", "predictor", "sequence", "item", "config", "reg_model",
    "reg_adapter", "callback", "tlm", "port", "component", "object", "interface",
    "class", "uvm",
]


def is_tlm_port(type_name: str) -> bool:
    return bool(TLM_PORT_RE.match(type_name))


def is_uvm_name(name: str) -> bool:
    return name.startswith("uvm_")


def looks_like_config(name: str) -> bool:
    return bool(re.search(r"(cfg|config)", name, re.IGNORECASE))

# Members of UVM base classes that user code commonly navigates through. Only
# used when the real library is not indexed. Types may name class parameters.
BUILTIN_MEMBERS: dict[str, dict[str, str]] = {
    "uvm_driver": {"seq_item_port": "uvm_seq_item_pull_port #(REQ, RSP)",
                   "rsp_port": "uvm_analysis_port #(RSP)", "req": "REQ", "rsp": "RSP"},
    "uvm_push_driver": {"req_export": "uvm_blocking_put_imp #(REQ, uvm_push_driver #(REQ, RSP))",
                        "rsp_port": "uvm_analysis_port #(RSP)", "req": "REQ", "rsp": "RSP"},
    "uvm_sequencer_param_base": {"rsp_export": "uvm_analysis_export #(RSP)"},
    "uvm_sequencer": {"seq_item_export": "uvm_seq_item_pull_imp #(REQ, RSP, uvm_sequencer #(REQ, RSP))"},
    "uvm_push_sequencer": {"req_port": "uvm_blocking_put_port #(REQ)"},
    "uvm_sequence": {"req": "REQ", "rsp": "RSP"},
    "uvm_sequence_base": {"m_sequencer": "uvm_sequencer_base"},
    "uvm_subscriber": {"analysis_export": "uvm_analysis_imp #(T, uvm_subscriber #(T))"},
    "uvm_tlm_analysis_fifo": {"analysis_export": "uvm_analysis_imp #(T, uvm_tlm_analysis_fifo #(T))"},
    "uvm_tlm_fifo_base": {"put_export": "uvm_put_imp #(T, uvm_tlm_fifo_base #(T))",
                          "get_export": "uvm_get_peek_imp #(T, uvm_tlm_fifo_base #(T))",
                          "get_peek_export": "uvm_get_peek_imp #(T, uvm_tlm_fifo_base #(T))",
                          "put_ap": "uvm_analysis_port #(T)", "get_ap": "uvm_analysis_port #(T)"},
}


def builtin_params(name: str) -> list[tuple[str, str]]:
    """[(param name, default)] for a built-in UVM class."""
    text = BUILTIN_CLASSES.get(name, (None, ""))[1]
    return [(m.group(1), m.group(2).strip())
            for m in re.finditer(r"(?:type|string|int)\s+(\w+)\s*=\s*([^,()]+(?:\([^)]*\))?)", text)]
