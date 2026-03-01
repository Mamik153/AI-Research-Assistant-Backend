# API Endpoint Changes

This document describes all API endpoints, their authentication requirements, request/response examples, and behavioral notes. Originally written for the v0.5.0 security remediation; updated for **v0.7.0** (SSE streaming, image filtering).

---

## Global Changes (All Protected Endpoints)

- **Authentication:** API key is required via `Authorization: Bearer <key>` or `X-API-Key: <key>`. If the server has no `API_KEY` configured or the key is wrong, the response is **401 Unauthorized** (previously: 501 when not configured).
- **Security headers:** Every response includes `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`, `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy`.
- **CORS:** Production origins limited to HTTPS only on `*.slickspender.com`; methods `GET`, `POST`, `OPTIONS`; headers `Authorization`, `X-API-Key`, `Content-Type` (no wildcards).
- **Request body size:** Requests with `Content-Length` > 1 MB receive **413 Payload Too Large**.

---

## 1. `GET /` — Root (unchanged)

**Auth:** None (still unauthenticated).

**Changes:** None.

**Example request:**
```http
GET / HTTP/1.1
Host: localhost:8000
```

**Example response:** `200 OK`
```json
{
  "message": "AI Research Backend API",
  "version": "0.7.0"
}
```

---

## 2. `POST /api/research` — Submit research job

**Auth:** Required (Bearer or X-API-Key).

**Changes:**
- **Topic validation:** `topic` must be 3–500 characters after sanitization (control characters stripped). Shorter or invalid topic returns **422 Unprocessable Entity**.
- **Body size:** Max 1 MB; otherwise **413**.

**Example request (valid):**
```http
POST /api/research HTTP/1.1
Host: localhost:8000
Authorization: Bearer your_api_key_here
Content-Type: application/json

{"topic": "quantum computing applications"}
```

**Example response:** `200 OK`
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "topic": "quantum computing applications"
}
```

**Example request (invalid topic — too short):**
```http
POST /api/research HTTP/1.1
Content-Type: application/json

{"topic": "ab"}
```

**Example response:** `422 Unprocessable Entity`
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "topic"],
      "msg": "Value error, Topic must be at least 3 characters after sanitization",
      "ctx": {"error": {}}
    }
  ]
}
```

**Example response (no/invalid API key):** `401 Unauthorized`
```json
{"detail": "Invalid or missing API key"}
```

---

## 3. `GET /api/research/{job_id}` — Get job status

**Auth:** Required.

**Changes:**
- **job_id validation:** Must be a valid UUID. Invalid format (e.g. `../../etc/passwd` or `not-a-uuid`) returns **400 Bad Request** with `"Invalid job ID format"`.

**Example request (valid):**
```http
GET /api/research/a1b2c3d4-e5f6-7890-abcd-ef1234567890 HTTP/1.1
Host: localhost:8000
X-API-Key: your_api_key_here
```

**Example response:** `200 OK`
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "topic": "quantum computing applications",
  "current_step": "Searching for papers",
  "progress_percentage": 30,
  "chain_of_thought": ["[10:15:32] AI agents are searching for relevant research papers"],
  "intermediate_findings": []
}
```

**Example request (invalid job_id):**
```http
GET /api/research/../../../etc/passwd HTTP/1.1
X-API-Key: your_api_key_here
```

**Example response:** `400 Bad Request`
```json
{"detail": "Invalid job ID format"}
```

**Example response (job not found):** `404 Not Found`
```json
{"detail": "Job not found"}
```

---

## 4. `GET /api/research/{job_id}/result` — Get research result

**Auth:** Required.

**Changes:**
- **job_id validation:** Same as above; invalid UUID → **400**.
- **Failed jobs:** When `status` is `failed`, the API returns a generic message instead of the stored error text. Response is **500** with `"Research job failed. Please try again later."` (no internal details).

**Example request (completed job):**
```http
GET /api/research/a1b2c3d4-e5f6-7890-abcd-ef1234567890/result HTTP/1.1
Host: localhost:8000
Authorization: Bearer your_api_key_here
```

**Example response:** `200 OK`
```json
{
  "report": "# Research Report\n\n...",
  "sources": ["https://arxiv.org/abs/..."],
  "completed_at": "2026-02-28T12:00:00",
  "jobId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "topic": "quantum computing applications"
}
```

**Example response (job failed):** `500 Internal Server Error`
```json
{"detail": "Research job failed. Please try again later."}
```
(Previously the `detail` could contain internal exception messages.)

**Example response (job still running):** `400 Bad Request`
```json
{"detail": "Job is still running. Please wait for completion."}
```

---

## 5. `POST /api/research/dynamic` — Submit dynamic research job

**Auth:** Required.

**Changes:** Same as `POST /api/research`: topic 3–500 chars (sanitized), max body 1 MB.

**Example request:**
```http
POST /api/research/dynamic HTTP/1.1
Host: localhost:8000
Authorization: Bearer your_api_key_here
Content-Type: application/json

{"topic": "transformer architectures in NLP"}
```

**Example response:** `200 OK`
```json
{
  "job_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012",
  "status": "pending",
  "topic": "transformer architectures in NLP"
}
```

---

## 6. `GET /api/research/dynamic/{job_id}/result` — Get dynamic research result

**Auth:** Required.

**Changes:**
- **job_id validation:** Must be a valid UUID → **400** if not.
- **Failed jobs:** Same generic **500** message: `"Research job failed. Please try again later."`

**Example request:**
```http
GET /api/research/dynamic/b2c3d4e5-f6a7-8901-bcde-f23456789012/result HTTP/1.1
Host: localhost:8000
X-API-Key: your_api_key_here
```

**Example response:** `200 OK` (structure unchanged; includes `topic`, `summary`, `papers`, `key_insights`, `generated_diagrams`, `structured_sections`, `section_confidence`, `section_images`, `completed_at`, `jobId`.)

**Example response (failed job):** `500 Internal Server Error`
```json
{"detail": "Research job failed. Please try again later."}
```

---

## 7. `GET /api/research/dynamic/{job_id}/stream` — SSE streaming (v0.7.0)

**Auth:** Required (Bearer or X-API-Key).

**Added in v0.7.0.** This endpoint opens a Server-Sent Events stream that pushes real-time progress, findings, and the final result for a dynamic research job. All existing polling endpoints continue to work — use them as a fallback if the SSE connection drops.

### How to connect

1. Submit a job via `POST /api/research/dynamic` (returns `job_id`).
2. Immediately open an SSE connection to `GET /api/research/dynamic/{job_id}/stream`.
3. Listen for events until you receive a `result`, `done`, or `error` event, then close.
4. If the connection drops, fall back to polling `GET /api/research/{job_id}` + `GET /api/research/dynamic/{job_id}/result`.

### Event types

| Event | Payload | Description |
|-------|---------|-------------|
| `progress` | `{"current_step": str, "progress_percentage": int, "message": str}` | Progress update — step name, percentage (0-100), and human-readable message. |
| `finding` | `{"finding": str}` | Intermediate finding discovered during research. |
| `result` | Full result JSON (same schema as `GET .../result` response) | The completed research result. Sent once when the job finishes. |
| `done` | `{"status": "completed"}` | Signals the stream is finished. Close the connection after this. |
| `error` | `{"message": str}` | An error occurred (job failed, timed out, etc.). Stream closes after this. |
| `: ping` | *(no data — SSE comment)* | Keepalive sent every ~15 s. Clients should ignore these. |

### Error responses (HTTP, before the stream opens)

| Status | Condition |
|--------|-----------|
| `400` | Job already `completed` or `failed` — use the result endpoint instead. |
| `400` | Invalid job ID format (not a UUID). |
| `401` | Missing or invalid API key. |
| `404` | Job not found. |
| `429` | Rate limit exceeded. |

### Example request

```http
GET /api/research/dynamic/b2c3d4e5-f6a7-8901-bcde-f23456789012/stream HTTP/1.1
Host: localhost:8000
Authorization: Bearer your_api_key_here
Accept: text/event-stream
```

### Example event stream

```
event: progress
data: {"current_step": "Initializing research", "progress_percentage": 5, "message": "Preparing to research the topic"}

event: progress
data: {"current_step": "Checking knowledge base", "progress_percentage": 10, "message": "Searching existing embeddings for relevant material"}

event: progress
data: {"current_step": "Searching ArXiv papers", "progress_percentage": 20, "message": "Searching for papers related to: transformer architectures"}

event: finding
data: {"finding": "Found 10 relevant research papers"}

event: progress
data: {"current_step": "Searching for research images", "progress_percentage": 48, "message": "Looking for diagrams and figures via web search"}

event: finding
data: {"finding": "Found 3 research images via web search"}

: ping

event: progress
data: {"current_step": "Running research agents", "progress_percentage": 55, "message": "Paper Analyzer extracting findings, Synthesis + Diagram agents starting"}

event: progress
data: {"current_step": "Generating visualizations", "progress_percentage": 80, "message": "Creating charts, concept maps, and rendering math expressions"}

event: progress
data: {"current_step": "Completed", "progress_percentage": 100, "message": "Dynamic research completed successfully"}

event: result
data: {"topic": "transformer architectures", "summary": "...", "papers": [...], "key_insights": [...], "generated_diagrams": [...], "structured_sections": {...}, "section_confidence": {...}, "section_images": {...}, "completed_at": "2026-03-01T12:00:00", "jobId": "b2c3d4e5-f6a7-8901-bcde-f23456789012"}

event: done
data: {"status": "completed"}
```

### JavaScript client example

```javascript
const jobId = "b2c3d4e5-f6a7-8901-bcde-f23456789012";
const eventSource = new EventSource(
  `https://your-api.com/api/research/dynamic/${jobId}/stream`,
  // Note: EventSource doesn't support custom headers natively.
  // Use a library like eventsource-polyfill or fetch-event-source for auth.
);

eventSource.addEventListener("progress", (e) => {
  const data = JSON.parse(e.data);
  console.log(`[${data.progress_percentage}%] ${data.current_step}: ${data.message}`);
});

eventSource.addEventListener("finding", (e) => {
  const data = JSON.parse(e.data);
  console.log("Finding:", data.finding);
});

eventSource.addEventListener("result", (e) => {
  const result = JSON.parse(e.data);
  console.log("Research complete:", result);
  eventSource.close();
});

eventSource.addEventListener("error", (e) => {
  // If this is an SSE error event with data, the job failed:
  if (e.data) {
    const data = JSON.parse(e.data);
    console.error("Job error:", data.message);
  }
  eventSource.close();
  // Fall back to polling...
});

eventSource.addEventListener("done", () => {
  eventSource.close();
});
```

### Fetch-based client example (with auth headers)

```javascript
import { fetchEventSource } from "@microsoft/fetch-event-source";

await fetchEventSource(
  `https://your-api.com/api/research/dynamic/${jobId}/stream`,
  {
    headers: {
      Authorization: "Bearer your_api_key_here",
    },
    onmessage(ev) {
      const data = JSON.parse(ev.data);
      switch (ev.event) {
        case "progress":
          updateProgressBar(data.progress_percentage, data.message);
          break;
        case "finding":
          appendFinding(data.finding);
          break;
        case "result":
          displayResult(data);
          break;
        case "error":
          showError(data.message);
          break;
      }
    },
    onerror(err) {
      console.error("SSE connection lost, falling back to polling", err);
      startPolling(jobId);
    },
  }
);
```

### Recommended client flow

```
1. POST /api/research/dynamic  →  get job_id
2. Try: GET /api/research/dynamic/{job_id}/stream (SSE)
   ├─ On "result" event  →  render result, close stream
   ├─ On "error" event   →  show error, close stream
   └─ On connection failure / timeout:
3. Fallback: Poll GET /api/research/{job_id} every 2s
   └─ When status == "completed":
4. GET /api/research/dynamic/{job_id}/result  →  render result
```

---

## 8. `GET /static/{file_path:path}` — Static files (replaced unauthenticated mount)

**Auth:** Required (breaking change).

**Changes:**
- **Before:** Static files under `/static/` were served without authentication (e.g. `GET /static/extracted_images/paper_0.png`).
- **After:** Same path is handled by a route that requires API key. Path is validated to prevent traversal (`..` or absolute paths → **400**). Missing file → **404**.

**Example request (valid, with auth):**
```http
GET /static/extracted_images/2301.12345_p0_i0.png HTTP/1.1
Host: localhost:8000
Authorization: Bearer your_api_key_here
```

**Example response:** `200 OK` (binary image body, with appropriate `Content-Type`).

**Example request (no auth):**
```http
GET /static/extracted_images/2301.12345_p0_i0.png HTTP/1.1
Host: localhost:8000
```

**Example response:** `401 Unauthorized`
```json
{"detail": "Invalid or missing API key"}
```

**Example request (path traversal attempt):**
```http
GET /static/../results/some-job.json HTTP/1.1
X-API-Key: your_api_key_here
```

**Example response:** `400 Bad Request`
```json
{"detail": "Invalid path"}
```

**Example request (file not found):**
```http
GET /static/nonexistent.png HTTP/1.1
X-API-Key: your_api_key_here
```

**Example response:** `404 Not Found`
```json
{"detail": "File not found"}
```

---

## Summary Table

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /` | None | Health check |
| `POST /api/research` | Required | `topic`: 3–500 chars, control chars stripped; body ≤ 1 MB |
| `GET /api/research/{job_id}` | Required | `job_id` must be UUID (400 if not); polling endpoint |
| `GET /api/research/{job_id}/result` | Required | `job_id` UUID; generic message on failure (500) |
| `POST /api/research/dynamic` | Required | Same as `POST /api/research` |
| `GET /api/research/dynamic/{job_id}/result` | Required | `job_id` UUID; generic message on failure (500) |
| `GET /api/research/dynamic/{job_id}/stream` | Required | **v0.7.0** — SSE stream; `text/event-stream`; events: progress, finding, result, done, error |
| `GET /static/{path}` | Required | Path traversal blocked; 400/404 on invalid path or missing file |

All protected endpoints receive security headers and are subject to CORS and request body size limits described at the top of this document.
