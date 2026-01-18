from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List

# If you want to run a snippet of code before or after the crew starts,
# you can use the @before_kickoff and @after_kickoff decorators
# https://docs.crewai.com/concepts/crews#example-crew-class-with-decorators
# from crewai_tools import TavilySearchTool
from ai_research_backend.tools.arxiv_tool import ArxivSearchTool
import litellm

# Compatibility patch for Groq - removes unsupported parameters
# Groq models may not support structured output parsing, so we remove those parameters
UNSUPPORTED_KEYS = [
    "is_litellm",
    "response_format",
    "structured_outputs",
    "json_schema",
]
_original_completion = litellm.completion


def _patched_completion(*args, **kwargs):
    """Monkey patch litellm.completion to remove unsupported parameters for Groq"""
    for key in UNSUPPORTED_KEYS:
        kwargs.pop(key, None)

    # Handle tool_choice parameter - if it's "none" but tools are provided, allow "auto"
    # This prevents "Tool choice is none, but model called a tool" errors
    if "tools" in kwargs and kwargs.get("tools") and len(kwargs["tools"]) > 0:
        if kwargs.get("tool_choice") == "none":
            # Allow tool calling when tools are provided
            kwargs["tool_choice"] = "auto"
        elif "tool_choice" not in kwargs:
            # Default to auto if tools are provided
            kwargs["tool_choice"] = "auto"

    return _original_completion(*args, **kwargs)


# Apply the patch
litellm.completion = _patched_completion

# Initialize Groq LLM
# Disable structured output to avoid parsing errors with Groq
groq_llm = LLM(
    model="groq/llama-3.3-70b-versatile",
    temperature=0.7,
    max_tokens=4096,
    # Disable structured output mode to prevent parsing errors
    # Groq models may not support structured output parsing
)

# Initialize tool
# tavily_tool = TavilySearchTool()
arxiv_tool = ArxivSearchTool()


@CrewBase
class AiResearchBackend:
    """AiResearchBackend crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    # Learn more about YAML configuration files here:
    # Agents: https://docs.crewai.com/concepts/agents#yaml-configuration-recommended
    # Tasks: https://docs.crewai.com/concepts/tasks#yaml-configuration-recommended

    # If you would like to add tools to your agents, you can learn more about it here:
    # https://docs.crewai.com/concepts/agents#agent-tools
    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["researcher"],  # type: ignore[index]
            tools=[arxiv_tool],
            llm=groq_llm,
            verbose=True,
        )

    @agent
    def reporting_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["reporting_analyst"],  # type: ignore[index]
            llm=groq_llm,
            verbose=True,
        )

    # To learn more about structured task outputs,
    # task dependencies, and task callbacks, check out the documentation:
    # https://docs.crewai.com/concepts/tasks#overview-of-a-task
    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config["research_task"],  # type: ignore[index]
        )

    @task
    def reporting_task(self) -> Task:
        return Task(
            config=self.tasks_config["reporting_task"],  # type: ignore[index]
            output_file="report.md",
        )

    @crew
    def crew(self) -> Crew:
        """Creates the AiResearchBackend crew"""
        # To learn how to add knowledge sources to your crew, check out the documentation:
        # https://docs.crewai.com/concepts/knowledge#what-is-knowledge

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,
            # process=Process.hierarchical, # In case you wanna use that instead https://docs.crewai.com/how-to/Hierarchical/
        )
