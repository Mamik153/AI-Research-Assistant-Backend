# Changelog

All notable changes to this project will be documented in this file.

Versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-02-26

### Minor change
- Added `section_confidence` to the dynamic research result JSON. Each of the 10 structured section keys (overview, key_concepts, benefits, risks, applications, future_directions, methodologies, comparisons, timeline, statistics) receives a confidence score between 0.0 and 1.0 indicating how well the section is supported by the retrieved papers.
- Added `section_images` to the dynamic research result JSON. Each section key maps to a list of image URLs — extracted paper figures assigned by the LLM, rendered LaTeX equations, and auto-generated data charts (statistics bar chart, comparison grouped bar chart).
- All image URLs in the API response (`papers[].images`, `section_images`) are now returned as absolute URLs using the configurable `API_BASE_URL` environment variable (defaults to `http://localhost:8000`).
- CORS now allows requests from any subdomain of `slickspender.com` in addition to `localhost:5173`.
- Added `matplotlib` dependency for chart and math rendering.

## [0.1.0] - Initial release

### Minor change
- Initial project setup with CrewAI-powered research agents, FastAPI server, ArXiv paper search, RAG-based paper chunking, and structured section output.
