# API Endpoint Changes (Security Remediation v0.5.0)

This document lists changes to each API endpoint introduced by the security remediation (v0.5.0) and includes request/response examples.

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
  "version": "1.0.0"
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

## 7. `GET /static/{file_path:path}` — Static files (replaced unauthenticated mount)

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

| Endpoint | Auth change | New validation / behavior |
|----------|-------------|---------------------------|
| `GET /` | No change | — |
| `POST /api/research` | — | `topic`: 3–500 chars, control chars stripped; body ≤ 1 MB |
| `GET /api/research/{job_id}` | — | `job_id` must be UUID (400 if not) |
| `GET /api/research/{job_id}/result` | — | `job_id` UUID; generic message on failure (500) |
| `POST /api/research/dynamic` | — | Same as `POST /api/research` |
| `GET /api/research/dynamic/{job_id}/result` | — | `job_id` UUID; generic message on failure (500) |
| `GET /static/{path}` | **Now required** | Path traversal blocked; 400/404 on invalid path or missing file |

All protected endpoints now receive security headers and are subject to CORS and request body size limits described at the top of this document.
