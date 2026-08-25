from mycoagent.cli import _parse_agent_option
from mycoagent.node.executor import ChildAgentExecutor
from mycoagent.node.llm import OpenAICompatClient
from mycoagent.node.runtime import AgentSpec, attach_spec_llms


def test_parse_agent_option_llm_fields():
    spec = _parse_agent_option(
        "name=gamma,skills=coding,llm_url=http://127.0.0.1:11434,llm_model=llama3,llm_key=sk-test"
    )
    assert spec.name == "gamma"
    assert spec.skills == ["coding"]
    assert spec.llm_base_url == "http://127.0.0.1:11434"
    assert spec.llm_model == "llama3"
    assert spec.llm_api_key == "sk-test"


def test_attach_spec_llms_uses_separate_clients():
    specs = [
        AgentSpec(name="a", llm_base_url="http://127.0.0.1:11434", llm_model="llama3"),
        AgentSpec(name="b", llm_base_url="http://127.0.0.1:4000", llm_model="gpt-4o-mini", llm_api_key="k"),
        AgentSpec(name="c"),
    ]
    attach_spec_llms(specs, max_steps=4)
    assert isinstance(specs[0].executor, ChildAgentExecutor)
    assert isinstance(specs[1].executor, ChildAgentExecutor)
    llm_a = specs[0].executor.llm
    llm_b = specs[1].executor.llm
    assert isinstance(llm_a, OpenAICompatClient)
    assert isinstance(llm_b, OpenAICompatClient)
    assert llm_a is not llm_b
    assert llm_a.base_url == "http://127.0.0.1:11434/v1"
    assert llm_a.model == "llama3"
    assert llm_b.base_url == "http://127.0.0.1:4000/v1"
    assert llm_b.api_key == "k"
    assert specs[2].executor is None
    assert specs[0].planner is not None
    assert specs[1].planner is not None
    assert specs[2].planner is None
