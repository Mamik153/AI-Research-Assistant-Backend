from crewai.tools import BaseTool
from typing import Type, List
from pydantic import BaseModel, Field
import arxiv
import fitz  # pymupdf
import os
import requests
from datetime import datetime


class ArxivSearchToolInput(BaseModel):
    """Input schema for ArxivSearchTool."""

    topic: str = Field(..., description="The research topic to search for on Arxiv.")


class ArxivSearchTool(BaseTool):
    name: str = "Arxiv Search Tool"
    description: str = (
        "Searches Arxiv for scientific papers related to a topic. "
        "It returns summaries and full text content from the top 5 most relevant papers."
    )
    args_schema: Type[BaseModel] = ArxivSearchToolInput

    def search_papers(self, topic: str) -> List[dict]:
        """Search Arxiv and return structured data"""
        try:
            # Search Arxiv
            search = arxiv.Search(
                query=topic, max_results=10, sort_by=arxiv.SortCriterion.Relevance
            )

            results = []

            # Ensure download directory exists
            download_dir = "downloaded_papers"
            os.makedirs(download_dir, exist_ok=True)

            # Ensure static images directory exists
            # We want this relative to the project root where api.py runs
            # Assuming api.py is run from ai_research_backend or parent
            static_dir = os.path.join(os.getcwd(), "static", "extracted_images")
            os.makedirs(static_dir, exist_ok=True)

            for result in search.results():
                paper_info = {
                    "title": result.title,
                    "authors": [a.name for a in result.authors],
                    "published": result.published.strftime("%Y-%m-%d"),
                    "summary": result.summary,
                    "pdf_url": result.pdf_url,
                    "content": "",
                    "images": [],
                }

                # Download PDF
                pdf_filename = f"{result.entry_id.split('/')[-1]}.pdf"
                pdf_path = os.path.join(download_dir, pdf_filename)

                # Check if we need to download it (simple cache check)
                if not os.path.exists(pdf_path):
                    result.download_pdf(dirpath=download_dir, filename=pdf_filename)

                # Extract text and images using PyMuPDF
                try:
                    doc = fitz.open(pdf_path)
                    text = ""
                    extracted_images = []

                    # extracting text from the first 5 pages to avoid token limits,
                    # usually introduction and methods are here.
                    # Adjust limit as needed for context window.
                    for page_index, page in enumerate(doc[:5]):
                        text += page.get_text()

                        # Extract images from page
                        image_list = page.get_images()
                        for img_index, img in enumerate(image_list):
                            xref = img[0]
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]

                            # Filter small icons/logos by size if possible, skipping for now to be safe
                            if len(image_bytes) < 1000:  # Skip very small images
                                continue

                            image_filename = f"{result.entry_id.split('/')[-1]}_p{page_index}_i{img_index}.{image_ext}"
                            image_path = os.path.join(static_dir, image_filename)

                            with open(image_path, "wb") as f:
                                f.write(image_bytes)

                            # Store relative path for API
                            extracted_images.append(
                                f"/static/extracted_images/{image_filename}"
                            )

                    paper_info["content"] = text
                    paper_info["images"] = extracted_images
                    doc.close()

                    # Clean up downloaded PDF file
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)

                except Exception as e:
                    paper_info["content"] = f"Error extracting text/images: {str(e)}"
                    print(f"Extraction error: {e}")

                results.append(paper_info)

            # Cleanup directory if empty
            if os.path.exists(download_dir) and not os.listdir(download_dir):
                os.rmdir(download_dir)

            return results
        except Exception as e:
            print(f"Error in search_papers: {e}")
            return []

    def _run(self, topic: str) -> str:
        try:
            results = self.search_papers(topic)

            # Format the output
            output_str = f"Found {len(results)} papers for topic '{topic}':\n\n"
            for i, paper in enumerate(results, 1):
                output_str += f"Paper {i}: {paper['title']}\n"
                output_str += f"Authors: {', '.join(paper['authors'])}\n"
                output_str += f"Published: {paper['published']}\n"
                output_str += f"URL: {paper['pdf_url']}\n"
                output_str += f"Summary: {paper['summary']}\n"
                output_str += f"Extracted Content (First 5 pages): {paper['content'][:2000]}...\n"  # Truncate for safety
                output_str += "-" * 50 + "\n"

            return output_str

        except Exception as e:
            return f"Error performing Arxiv search: {str(e)}"

        except Exception as e:
            return f"Error performing Arxiv search: {str(e)}"
