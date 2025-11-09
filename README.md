# AiResearchBackend Crew

Welcome to the AiResearchBackend Crew project, powered by [crewAI](https://crewai.com). This template is designed to help you set up a multi-agent AI system with ease, leveraging the powerful and flexible framework provided by crewAI. Our goal is to enable your agents to collaborate effectively on complex tasks, maximizing their collective intelligence and capabilities.

## Installation

Ensure you have Python >=3.10 <3.14 installed on your system. This project uses [UV](https://docs.astral.sh/uv/) for dependency management and package handling, offering a seamless setup and execution experience.

First, if you haven't already, install uv:

```bash
pip install uv
```

Next, navigate to your project directory and install the dependencies:

```bash
cd ai_research_backend
uv sync
```

### Customizing

**Add your `GEMINI_API_KEY` into the `.env` file**

- Modify `src/ai_research_backend/config/agents.yaml` to define your agents
- Modify `src/ai_research_backend/config/tasks.yaml` to define your tasks
- Modify `src/ai_research_backend/crew.py` to add your own logic, tools and specific args
- Modify `src/ai_research_backend/main.py` to add custom inputs for your agents and tasks

## Running the Project

### Running the CrewAI Crew Directly

To kickstart your crew of AI agents and begin task execution, run this from the root folder of your project:

```bash
$ crewai run
```

This command initializes the ai-research-backend Crew, assembling the agents and assigning them tasks as defined in your configuration.

This example, unmodified, will run the create a `report.md` file with the output of a research on LLMs in the root folder.

### Running the FastAPI Server

To start the FastAPI server for the research API endpoints:

```bash
uv run uvicorn src.ai_research_backend.api:app --host 0.0.0.0 --port 8000 --reload
```

Or using the script entry point:

```bash
uv run run_api
```

The API will be available at `http://localhost:8000` with the following endpoints:

- **POST** `/api/research` - Submit a research job with a topic
- **GET** `/api/research/{job_id}` - Get the status of a research job
- **GET** `/api/research/{job_id}/result` - Get the research result when completed

The API supports CORS for frontend requests from `http://localhost:5173`.

## Understanding Your Crew

The ai-research-backend Crew is composed of multiple AI agents, each with unique roles, goals, and tools. These agents collaborate on a series of tasks, defined in `config/tasks.yaml`, leveraging their collective skills to achieve complex objectives. The `config/agents.yaml` file outlines the capabilities and configurations of each agent in your crew.

## Support

For support, questions, or feedback regarding the AiResearchBackend Crew or crewAI.
- Visit our [documentation](https://docs.crewai.com)
- Reach out to us through our [GitHub repository](https://github.com/joaomdmoura/crewai)
- [Join our Discord](https://discord.com/invite/X4JWnZnxPb)
- [Chat with our docs](https://chatg.pt/DWjSBZn)

Let's create wonders together with the power and simplicity of crewAI.
