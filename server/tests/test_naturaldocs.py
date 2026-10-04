from uvmls.naturaldocs import markdown, plain, topic, untitled


def test_topic_picks_the_part_about_a_name():
    text = "Group: Construction\n\nFunction: new\n\nCreates one.\n\nFunction- m_init\nInternal."
    assert topic(text, "new") == "Creates one."
    assert topic(text, "m_init") == "Internal."
    assert topic(text, "run") is None
    assert topic("CLASS: uvm_driver #(REQ,RSP)\n\nDrives.", "uvm_driver") == "Drives."
    assert topic("type-based ordering\nFunction: f", "based") is None  # prose, not a topic
    assert untitled("Function: get_uvmargs\n\nReturns the arguments.") == "Returns the arguments."
    assert untitled("Plain text.") == "Plain text."


def test_markdown_reads_as_the_comment_does():
    doc = ("The <set> and <get> methods mirror <uvm_component::set_config_int>; *Note* the ~name~\n"
           "argument. The set/get_config_* calls, a < b and _x_ are text.\n\n"
           "|  uvm_config_db#(int)::set(this, \"*\", \"A\");\n"
           "|    nested();\n\n"
           "name - Instance name\n"
           "scope - An optional scope\n"
           "that goes on.\n\n"
           "- one\n- two\n"
           "---\n#define")
    assert markdown(doc) == (
        "The `set` and `get` methods mirror `uvm_component::set_config_int`; **Note** the `name`\n"
        "argument. The set/get_config\\_\\* calls, a \\< b and \\_x\\_ are text.\n\n"
        "```systemverilog\nuvm_config_db#(int)::set(this, \"*\", \"A\");\n  nested();\n```\n\n"
        "- **name** – Instance name\n"
        "- **scope** – An optional scope\n"
        "that goes on.\n\n"
        "- one\n- two\n"
        "\\---\n\\#define")


def test_plain_text_joins_paragraphs():
    doc = "The ~rsp_port~ needs\nconnecting to <uvm_sequencer>.\n\n|  a.connect(b);\n\nname - Instance\nname - again"
    assert plain(doc) == ("The rsp_port needs connecting to uvm_sequencer.\n\n    a.connect(b);\n\n"
                          "name - Instance\nname - again")
