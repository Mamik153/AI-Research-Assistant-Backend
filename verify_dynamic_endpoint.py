import requests
import time
import json
import sys

BASE_URL = "http://localhost:8000"


def test_dynamic_research():
    print("1. Submitting dynamic research job...")
    topic = "Multimodal AI Agents"
    try:
        response = requests.post(
            f"{BASE_URL}/api/research/dynamic", json={"topic": topic}
        )
        response.raise_for_status()
        job_data = response.json()
        job_id = job_data["job_id"]
        print(f"   Job submitted successfully. Job ID: {job_id}")
    except requests.exceptions.ConnectionError:
        print("   Error: Could not connect to API. Is it running?")
        sys.exit(1)
    except Exception as e:
        print(f"   Error submitting job: {e}")
        sys.exit(1)

    print("2. Polling for completion...")
    while True:
        status_response = requests.get(f"{BASE_URL}/api/research/{job_id}")
        if status_response.status_code != 200:
            print(f"   Error checking status: {status_response.text}")
            break

        status_data = status_response.json()
        status = status_data["status"]
        print(f"   Status: {status}")

        if status == "completed":
            break
        elif status == "failed":
            print("   Job failed!")
            # Try to get error details
            try:
                result_response = requests.get(
                    f"{BASE_URL}/api/research/dynamic/{job_id}/result"
                )
                print(f"   Error details: {result_response.text}")
            except:
                pass
            sys.exit(1)

        time.sleep(2)

    print("3. Fetching results...")
    result_response = requests.get(f"{BASE_URL}/api/research/dynamic/{job_id}/result")
    if result_response.status_code == 200:
        result = result_response.json()
        print("\n--- Dynamic Research Result ---")
        print(f"Topic: {result['topic']}")
        print(f"Summary Start: {result.get('summary', '')[:200]}...")
        print(f"Papers Found: {len(result.get('papers', []))}")

        # Verify Images
        total_images = 0
        if "papers" in result:
            for p in result["papers"]:
                if "images" in p:
                    total_images += len(p["images"])
                    if p["images"]:
                        print(
                            f"   - Paper '{p['title'][:30]}...' has {len(p['images'])} images. Example: {p['images'][0]}"
                        )
        print(f"Total Extracted Images: {total_images}")

        print("\nKey Insights:")
        for insight in result.get("key_insights", [])[:3]:
            print(f" - {insight}")

        print(f"\nGenerated Diagrams: {len(result.get('generated_diagrams', []))}")
        if result.get("generated_diagrams"):
            print(f"First Diagram Code:\n{result['generated_diagrams'][0][:100]}...")

        # Verify structure
        if "papers" in result and isinstance(result["papers"], list):
            print("\n✅ Verification Successful: 'papers' list present.")
        else:
            print("\n❌ Verification Failed: 'papers' list missing or invalid.")

    else:
        print(f"   Error fetching result: {result_response.text}")


if __name__ == "__main__":
    test_dynamic_research()
