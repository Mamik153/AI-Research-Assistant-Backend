import logging
from typing import Type, List

import arxiv
import fitz  # pymupdf
import os
import tempfile
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

from ai_research_backend.storage import upload_file
from ai_research_backend.image_filter import is_research_relevant, is_header_region

logger = logging.getLogger(__name__)

_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}


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
        """Search Arxiv and return structured data."""
        try:
            search = arxiv.Search(
                query=topic, max_results=10, sort_by=arxiv.SortCriterion.Relevance
            )

            results = []

            for result in search.results():
                arxiv_id = result.entry_id.split("/")[-1]
                paper_info = {
                    "title": result.title,
                    "arxiv_id": arxiv_id,
                    "authors": [a.name for a in result.authors],
                    "published": result.published.strftime("%Y-%m-%d"),
                    "summary": result.summary,
                    "pdf_url": result.pdf_url,
                    "content": "",
                    "images": [],
                    "pdf_storage_path": "",
                }

                with tempfile.TemporaryDirectory() as tmpdir:
                    pdf_filename = f"{arxiv_id}.pdf"
                    pdf_path = os.path.join(tmpdir, pdf_filename)
                    result.download_pdf(dirpath=tmpdir, filename=pdf_filename)

                    try:
                        doc = fitz.open(pdf_path)
                        text = ""
                        extracted_images: List[str] = []

                        for page_index, page in enumerate(doc[:5]):
                            text += page.get_text()
                            page_height = page.rect.height

                            image_list = page.get_images(full=True)
                            for img_index, img in enumerate(image_list):
                                xref = img[0]
                                base_image = doc.extract_image(xref)
                                image_bytes = base_image["image"]
                                image_ext = base_image["ext"]

                                if not is_research_relevant(image_bytes):
                                    continue

                                bbox = page.get_image_bbox(img)
                                if bbox and is_header_region(page_index, tuple(bbox), page_height):
                                    continue

                                storage_path = f"extracted_images/{arxiv_id}_p{page_index}_i{img_index}.{image_ext}"
                                mime = _EXT_TO_MIME.get(image_ext, "application/octet-stream")

                                try:
                                    url = upload_file(storage_path, image_bytes, mime)
                                    extracted_images.append(url)
                                except Exception as exc:
                                    logger.warning("Image upload failed: %s", exc)

                        paper_info["content"] = text
                        paper_info["images"] = extracted_images
                        doc.close()

                        with open(pdf_path, "rb") as f:
                            pdf_bytes = f.read()
                        pdf_storage_path = f"pdfs/{arxiv_id}.pdf"
                        try:
                            upload_file(pdf_storage_path, pdf_bytes, "application/pdf")
                            paper_info["pdf_storage_path"] = pdf_storage_path
                        except Exception as exc:
                            logger.warning("PDF upload failed for %s: %s", arxiv_id, exc)

                    except Exception as e:
                        paper_info["content"] = f"Error extracting text/images: {str(e)}"
                        logger.error("Extraction error for %s: %s", arxiv_id, e)

                results.append(paper_info)

            return results
        except Exception as e:
            logger.error("Error in search_papers: %s", e)
            return []

    def _run(self, topic: str) -> str:
        try:
            results = self.search_papers(topic)

            output_str = f"Found {len(results)} papers for topic '{topic}':\n\n"
            for i, paper in enumerate(results, 1):
                output_str += f"Paper {i}: {paper['title']}\n"
                output_str += f"Authors: {', '.join(paper['authors'])}\n"
                output_str += f"Published: {paper['published']}\n"
                output_str += f"URL: {paper['pdf_url']}\n"
                output_str += f"Summary: {paper['summary']}\n"
                output_str += f"Extracted Content (First 5 pages): {paper['content'][:2000]}...\n"
                output_str += "-" * 50 + "\n"

            return output_str

        except Exception as e:
            return f"Error performing Arxiv search: {str(e)}"
