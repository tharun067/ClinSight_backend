# ClinSight — Medical Diagnosis RAG Backend

AI-powered clinical decision support system built with **FastAPI**, **PostgreSQL**, **FAISS**, **Neo4j (SNOMED CT)**, **BioBERT**, **BiomedCLIP**, **Groq (Mixtral)**, and **Google Gemini**.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Tech Stack](#tech-stack)
3. [Setup & Installation](#setup--installation)
4. [Environment Variables](#environment-variables)
5. [Running the Server](#running-the-server)
6. [Authentication & Roles](#authentication--roles)
7. [API Endpoints Reference](#api-endpoints-reference)
   - [Health & Root](#health--root)
   - [Authentication (`/api/auth`)](#authentication-apiauth)
   - [Patients (`/api/patients`)](#patients-apipatients)
   - [AI Diagnostics (`/api/diagnostic`)](#ai-diagnostics-apidiagnostic)
   - [Documents (`/api/documents`)](#documents-apidocuments)
   - [Imaging Studies (`/api/imaging`)](#imaging-studies-apiimaging)
   - [Labs & Vitals (`/api/labs`)](#labs--vitals-apilabs)
   - [Clinical Notes (`/api/notes`)](#clinical-notes-apinotes)
   - [Audit Logs (`/api/audit`)](#audit-logs-apiaudit)
8. [Database Reset](#database-reset)
9. [Interactive API Docs](#interactive-api-docs)

---

## Architecture Overview

```
Client / Frontend
      │
      ▼
FastAPI (uvicorn)
      │
      ├── PostgreSQL (patient records, labs, notes, users)
      ├── FAISS     (vector similarity search for RAG)
      ├── Neo4j     (SNOMED CT knowledge graph)
      ├── BioBERT   (clinical text embeddings)
      ├── BiomedCLIP (medical image embeddings)
      ├── Groq / Mixtral (fast text: entity extraction, summaries, differentials)
      └── Google Gemini (multimodal: full diagnostic reports, image analysis)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI 0.115 + Uvicorn |
| Database | PostgreSQL + SQLAlchemy (async) |
| Vector DB | FAISS (local, CPU) |
| Knowledge Graph | Neo4j (SNOMED CT) |
| Auth | JWT (python-jose) + bcrypt |
| Text Embedding | BioBERT (`dmis-lab/biobert-v1.1`) |
| Image Embedding | BiomedCLIP (`microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224`) |
| LLM (fast/cheap) | Groq API — Mixtral-8x7B |
| LLM (multimodal) | Google Gemini 1.5 Pro |
| File Processing | PyMuPDF, pytesseract, pydicom, OpenCV |

---

## Setup & Installation

```bash
# 1. Clone / extract the project
cd medical_diagnosis_rag

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in the environment file
cp .env.example .env
# Edit .env — see Environment Variables below

# 5. Ensure PostgreSQL is running and the database exists
createdb clinsight_db

# 6. (Optional) Reset / initialise the database schema
python reset_database.py

# 7. Start the server
python run.py --dev
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
# ── Required ──────────────────────────────────────────────────────────────────
SECRET_KEY=your-secret-key-minimum-32-characters-long
POSTGRES_PASSWORD=your_postgres_password

# ── Optional — override defaults ──────────────────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_DB=clinsight_db

# Full connection URL (overrides individual POSTGRES_* vars if set)
# DATABASE_URL=postgresql://user:pass@host:5432/dbname

NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password

GOOGLE_API_KEY=your_google_api_key
GROQ_API_KEY=your_groq_api_key

DEBUG=false
LOG_LEVEL=INFO
```

---

## Running the Server

`main.py` is the single entry point — no separate launcher needed.

```bash
# Default  (127.0.0.1:8000)
python -m src.main

# Bind to all interfaces (needed for Docker / remote access)
python -m src.main --host 0.0.0.0

# Custom port
python -m src.main --port 8080

# Auto-reload on code changes (development)
python -m src.main --reload

# Or call uvicorn directly — fully equivalent
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Set `DEBUG=True` in `.env` to enable detailed error responses and auto-reload by default.

---

## Authentication & Roles

All protected endpoints require a **Bearer JWT token** in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

### Roles

| Role | Capabilities |
|---|---|
| `admin` | Full access to every endpoint; can manage users |
| `physician` | Read/write patients, diagnose, interpret imaging, manage notes |
| `nurse` | Register patients, record labs/vitals, upload documents, add notes |
| `patient` | View and update own record only |

> **Note:** Admin bypasses all role checks — admin can call any endpoint.

---

## API Endpoints Reference

---

### Health & Root

#### `GET /health`
**Public.** Server health check. Returns status, app name, version, and dev mode flag.

```bash
curl http://localhost:8000/health
```
```json
{ "status": "healthy", "app": "ClinSight - Medical Diagnosis Support System", "version": "1.0.0", "dev_mode": false }
```
**When to use:** Load balancer health checks, monitoring, confirming the server is up.

---

#### `GET /`
**Public.** Root welcome message with links to docs and health endpoint.

---

#### `GET /api/debug/logs?lines=100`
**Dev mode only.** Returns the last N lines of `clinsight_api.log`. Returns 403 in production.

---

### Authentication (`/api/auth`)

#### `POST /api/auth/register`
**Public.** Self-registration for **patients only**.

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"john_doe","email":"john@example.com","full_name":"John Doe","password":"SecurePass123"}'
```
```json
{ "uuid": "...", "username": "john_doe", "email": "john@example.com", "role": "patient", "is_active": true, "created_at": "..." }
```
**When to use:** Patient self-sign-up via a frontend registration form.

---

#### `POST /api/auth/bootstrap/admin`
**Public (one-time only).** Creates the first admin account. Fails if any admin already exists.

```bash
curl -X POST http://localhost:8000/api/auth/bootstrap/admin \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@hospital.com","full_name":"System Admin","password":"AdminPass123!","role":"admin"}'
```
**When to use:** Initial system setup — run once after deploying to create the first admin.

---

#### `POST /api/auth/register/staff`
**Admin only.** Register a physician, nurse, or another admin.

```bash
curl -X POST http://localhost:8000/api/auth/register/staff \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"username":"dr_smith","email":"dr.smith@hospital.com","full_name":"Dr. Smith","password":"DocPass456!","role":"physician"}'
```
**When to use:** Hospital IT admin onboarding clinical staff.

---

#### `POST /api/auth/login`
**Public.** Returns a JWT access token (valid for 15 minutes by default).

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"dr_smith","password":"DocPass456!"}'
```
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "uuid": "...", "username": "dr_smith", "role": "physician", ... }
}
```
**When to use:** Frontend login form — store the returned token for subsequent requests.

---

#### `GET /api/auth/me`
**All authenticated roles.** Returns the current user's profile.

```bash
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer <token>"
```
**When to use:** Frontend "who am I?" check after login, populating user profile pages.

---

#### `GET /api/auth/users`
**Admin only.** Lists all registered users.

---

#### `GET /api/auth/users/{user_id}`
**Admin only.** Get a specific user by UUID.

---

#### `PATCH /api/auth/users/{user_id}/role?new_role=physician`
**Admin only.** Change a user's role.

```bash
curl -X PATCH "http://localhost:8000/api/auth/users/UUID/role?new_role=physician" \
  -H "Authorization: Bearer <admin_token>"
```
**When to use:** Promoting a nurse to physician, or correcting a role assignment.

---

#### `PATCH /api/auth/users/{user_id}/deactivate`
**Admin only.** Deactivate a user (cannot deactivate yourself).

---

#### `PATCH /api/auth/users/{user_id}/activate`
**Admin only.** Reactivate a previously deactivated user.

---

#### `DELETE /api/auth/users/{user_id}`
**Admin only.** Permanently delete a user account (cannot delete yourself).

---

### Patients (`/api/patients`)

#### `POST /api/patients/`
**Nurse, Admin.** Register a new patient. Auto-generates a unique MRN (`MRN-XXXXX`).

```bash
curl -X POST http://localhost:8000/api/patients/ \
  -H "Authorization: Bearer <nurse_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Jane Doe",
    "date_of_birth": "1985-04-12",
    "gender": "Female",
    "phone": "+1-555-0100",
    "email": "jane.doe@email.com",
    "visit_type": "Outpatient",
    "chief_complaint": "Persistent cough and fever"
  }'
```
```json
{ "uuid": "...", "mrn": "MRN-47832", "full_name": "Jane Doe", "status": "Active", ... }
```
**When to use:** Nurse admitting a new patient at registration desk.

---

#### `GET /api/patients/?page=1&page_size=10&search=Jane&status_filter=Active`
**Nurse, Physician, Admin.** Paginated patient list with search and status filter.

```bash
curl "http://localhost:8000/api/patients/?search=Jane&page=1&page_size=20" \
  -H "Authorization: Bearer <physician_token>"
```
**When to use:** Patient search / list dashboard, ward overview.

---

#### `GET /api/patients/{patient_id}`
**Nurse, Physician, Admin, Patient (own only).** Get full patient details.

---

#### `PUT /api/patients/{patient_id}`
**Nurse, Physician, Admin.** Update patient record fields.

```bash
curl -X PUT http://localhost:8000/api/patients/UUID \
  -H "Authorization: Bearer <nurse_token>" \
  -H "Content-Type: application/json" \
  -d '{"chief_complaint": "Worsening shortness of breath", "phone": "+1-555-0199"}'
```

---

#### `PATCH /api/patients/{patient_id}/status?new_status=Discharged`
**Nurse, Physician, Admin.** Update patient status (`Active`, `Pending`, `Discharged`).

```bash
curl -X PATCH "http://localhost:8000/api/patients/UUID/status?new_status=Discharged" \
  -H "Authorization: Bearer <physician_token>"
```
**When to use:** Discharging a patient from the ward.

---

#### `DELETE /api/patients/{patient_id}`
**Admin only.** Permanently delete a patient record.

---

#### `GET /api/patients/my-record`
**Patient only.** Get own linked medical record.

---

#### `PATCH /api/patients/my-record`
**Patient only.** Update own contact details (phone, address, email).

---

#### `POST /api/patients/link-my-record?mrn=MRN-47832`
**Patient only.** Link a patient account to an existing medical record by MRN. The email on file must match the account email.

```bash
curl -X POST "http://localhost:8000/api/patients/link-my-record?mrn=MRN-47832" \
  -H "Authorization: Bearer <patient_token>"
```
**When to use:** After a patient self-registers, they link their account to their clinic record so they can view their history.

---

### AI Diagnostics (`/api/diagnostic`)

#### `POST /api/diagnostic/generate`
**Physician, Admin.** Run the full RAG diagnostic pipeline for a patient.

The pipeline: BioBERT embeds the clinical query → FAISS retrieves similar historical cases → Neo4j queries SNOMED CT for related conditions → Groq extracts entities and generates differential diagnoses → Gemini synthesizes the full report → Groq generates an executive summary.

```bash
curl -X POST http://localhost:8000/api/diagnostic/generate \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "PATIENT-UUID",
    "query": "Patient presents with fever, productive cough, and consolidation on chest X-ray",
    "include_images": true
  }'
```
```json
{
  "uuid": "...",
  "title": "AI Diagnostic Analysis — Jane Doe",
  "summary": "Based on the clinical presentation and imaging...",
  "suggested_conditions": [
    { "condition": "Community-Acquired Pneumonia", "confidence": 0.87, "icd10": "J18.9" }
  ],
  "evidence_summary": "...",
  "citations": [ ... ],
  "created_at": "..."
}
```
**When to use:** Physician needs AI-assisted differential diagnosis for a complex presentation. The system pulls relevant past cases, knowledge graph relationships, and imaging findings.

---

#### `GET /api/diagnostic/reports/{patient_id}`
**Physician, Admin, Patient (own only).** List all AI diagnostic reports for a patient.

---

#### `GET /api/diagnostic/reports/detail/{report_id}`
**Physician, Admin, Patient (own only).** Get full details of a single diagnostic report.

---

#### `POST /api/diagnostic/analyze-image`
**Physician, Admin.** Analyze a medical image with Gemini Vision.

```bash
curl -X POST http://localhost:8000/api/diagnostic/analyze-image \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{"image_path": "./uploads/abc123.png", "query": "Are there signs of pneumothorax?"}'
```
```json
{ "image_path": "...", "analysis": "The image shows...", "model": "gemini-1.5-pro" }
```
**When to use:** Quick AI second opinion on a DICOM/PNG image without generating a full patient report.

---

#### `POST /api/diagnostic/summarize-note`
**Physician, Nurse, Admin.** Summarize a block of clinical text using Groq.

```bash
curl -X POST http://localhost:8000/api/diagnostic/summarize-note \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{"note_text": "Patient is a 45-year-old male presenting with...", "max_length": 150}'
```
```json
{ "original_length": 312, "summary_length": 48, "summary": "45M with...", "model": "mixtral-8x7b-32768" }
```
**When to use:** Quickly condensing lengthy discharge summaries or referral letters for handover.

---

#### `POST /api/diagnostic/extract-entities`
**Physician, Nurse, Admin.** Extract medical entities (symptoms, diagnoses, medications) from free text.

```bash
curl -X POST http://localhost:8000/api/diagnostic/extract-entities \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{"text": "Patient has Type 2 diabetes, takes metformin 500mg, and presents with polyuria and polydipsia."}'
```
```json
{
  "text_length": 89,
  "entities": {
    "diagnoses": ["Type 2 diabetes"],
    "medications": ["metformin 500mg"],
    "symptoms": ["polyuria", "polydipsia"]
  },
  "model": "mixtral-8x7b-32768"
}
```
**When to use:** Structuring unstructured clinical notes for coding, billing, or populating structured fields.

---

#### `GET /api/diagnostic/capabilities`
**Physician, Admin.** List all available AI models and features with their use cases.

---

### Documents (`/api/documents`)

#### `POST /api/documents/upload`
**Nurse, Physician, Admin, Patient (own only).** Upload a document for a patient. AI extraction (OCR + structured data parsing) is automatically queued for PDFs and images.

```bash
curl -X POST http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer <nurse_token>" \
  -F "file=@lab_results.pdf" \
  -F "patient_id=PATIENT-UUID" \
  -F "document_type=Lab Results" \
  -F "notes=External lab from City Hospital"
```
```json
{
  "uuid": "DOC-UUID",
  "original_filename": "lab_results.pdf",
  "document_type": "Lab Results",
  "file_size": 204800,
  "patient_id": "PATIENT-UUID",
  "upload_date": "...",
  "extraction_status": "pending"
}
```
**When to use:** Nurse uploading a patient's external lab PDF or photo ID at admission.

---

#### `POST /api/documents/my-documents/upload`
**Patient only.** Upload a document to own record (no need to specify `patient_id`).

---

#### `POST /api/documents/bulk-upload`
**Nurse, Admin.** Upload multiple documents at once. `document_types` is a comma-separated list matching the file order.

```bash
curl -X POST http://localhost:8000/api/documents/bulk-upload \
  -H "Authorization: Bearer <nurse_token>" \
  -F "files=@id_card.jpg" \
  -F "files=@insurance.pdf" \
  -F "patient_id=PATIENT-UUID" \
  -F "document_types=ID / Passport,Insurance Card"
```
**When to use:** Batch scanning and uploading of a patient's paperwork at admission.

---

#### `GET /api/documents/{document_id}/extraction-status`
**Nurse, Physician, Admin, Patient (own only).** Poll AI extraction progress and results.

Extraction statuses: `pending` → `processing` → `completed` / `failed` / `not_started`

```bash
curl http://localhost:8000/api/documents/DOC-UUID/extraction-status \
  -H "Authorization: Bearer <nurse_token>"
```
```json
{
  "document_id": "DOC-UUID",
  "extraction_status": "completed",
  "results": {
    "labs_extracted": 5,
    "vitals_extracted": 2,
    "imaging_extracted": 0,
    "lab_ids": ["LAB-UUID-1", "LAB-UUID-2", ...],
    "vital_ids": ["VIT-UUID-1", ...],
    "raw_text_length": 4200,
    "ai_provider": "gemini"
  }
}
```
**When to use:** After uploading a lab PDF, poll this endpoint every few seconds until `completed` to see what was auto-extracted into the structured records.

---

#### `GET /api/documents/patient/{patient_id}/extractions?status_filter=completed`
**Nurse, Physician, Admin, Patient (own only).** All document extraction results for a patient, optionally filtered by status.

**When to use:** Dashboard showing what AI has pulled from all uploaded documents for a patient review.

---

#### `GET /api/documents/?patient_id=UUID&document_type=Lab+Results`
**All authenticated roles.** List documents with optional filters. Patients see only their own.

---

#### `GET /api/documents/my-documents`
**Patient only.** Get own documents.

---

#### `GET /api/documents/{document_id}`
**All authenticated roles.** Get document details including extraction status.

---

#### `GET /api/documents/{document_id}/download`
**All authenticated roles.** Download the original file.

```bash
curl -O -J http://localhost:8000/api/documents/DOC-UUID/download \
  -H "Authorization: Bearer <token>"
```

---

#### `DELETE /api/documents/{document_id}`
**Physician, Admin.** Delete document and its physical file.

---

### Imaging Studies (`/api/imaging`)

#### `POST /api/imaging/?patient_id=UUID`
**Nurse, Physician, Admin.** Create an imaging study record.

```bash
curl -X POST "http://localhost:8000/api/imaging/?patient_id=PATIENT-UUID" \
  -H "Authorization: Bearer <nurse_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "study_date": "2025-03-01T10:30:00",
    "modality": "CT",
    "body_part": "Chest",
    "description": "CT chest with contrast — rule out PE"
  }'
```
```json
{ "uuid": "STUDY-UUID", "modality": "CT", "body_part": "Chest", "status": "pending", ... }
```
**When to use:** Nurse ordering or recording an imaging study.

---

#### `GET /api/imaging/?patient_id=UUID&modality=CT&status_filter=pending`
**All roles.** List imaging studies with optional filters. Patients see only their own.

---

#### `GET /api/imaging/my-imaging`
**Patient only.** Get own imaging studies.

---

#### `GET /api/imaging/patient/{patient_id}`
**All authenticated roles.** All imaging studies for a specific patient.

---

#### `GET /api/imaging/{study_id}`
**All authenticated roles.** Get a single imaging study.

---

#### `PUT /api/imaging/{study_id}/interpret`
**Physician, Admin.** Add radiologist findings and impression to a study. Automatically embeds findings into FAISS for future RAG retrieval.

```bash
curl -X PUT http://localhost:8000/api/imaging/STUDY-UUID/interpret \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "findings": "Bilateral ground-glass opacities in lower lobes consistent with atypical pneumonia",
    "impression": "Findings suggest community-acquired pneumonia, likely atypical",
    "status": "complete"
  }'
```
**When to use:** Radiologist or physician reporting on a scan — their findings become searchable via RAG.

---

#### `PUT /api/imaging/{study_id}`
**Nurse, Physician, Admin.** Update study metadata.

---

#### `DELETE /api/imaging/{study_id}`
**Physician, Admin.** Delete an imaging study record.

---

### Labs & Vitals (`/api/labs`)

#### `POST /api/labs/labs?patient_id=UUID`
**Nurse, Physician, Admin.** Add a lab result. Abnormal flag is auto-calculated from reference ranges.

```bash
curl -X POST "http://localhost:8000/api/labs/labs?patient_id=PATIENT-UUID" \
  -H "Authorization: Bearer <nurse_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "test_name": "CRP",
    "test_value": 45.2,
    "unit": "mg/L",
    "test_date": "2025-03-01T09:00:00",
    "reference_range_low": 0,
    "reference_range_high": 10,
    "notes": "Elevated, consistent with acute infection"
  }'
```
```json
{ "uuid": "...", "test_name": "CRP", "test_value": 45.2, "is_abnormal": "High", ... }
```
**When to use:** Nurse entering lab results from the hospital lab system.

---

#### `GET /api/labs/labs?patient_id=UUID&test_name=CRP`
**All roles.** List lab results with optional filters.

---

#### `GET /api/labs/labs/patient/{patient_id}`
**All roles.** All labs for a specific patient.

---

#### `GET /api/labs/labs/{lab_id}`
**All roles.** Get a single lab result.

---

#### `PUT /api/labs/labs/{lab_id}`
**Nurse, Physician, Admin.** Update a lab result.

---

#### `DELETE /api/labs/labs/{lab_id}`
**Nurse, Physician, Admin.** Delete a lab result.

---

#### `POST /api/labs/my-labs`
**Patient only.** Patient adds own external lab result (e.g., from home testing kit).

---

#### `GET /api/labs/my-labs`
**Patient only.** Get own lab results.

---

#### `POST /api/labs/vitals?patient_id=UUID`
**Nurse, Physician, Admin.** Record vital signs. BMI is auto-calculated when height and weight are provided.

```bash
curl -X POST "http://localhost:8000/api/labs/vitals?patient_id=PATIENT-UUID" \
  -H "Authorization: Bearer <nurse_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "measurement_date": "2025-03-01T08:00:00",
    "temperature": 38.6,
    "temperature_unit": "°C",
    "systolic_bp": 128,
    "diastolic_bp": 82,
    "heart_rate": 96,
    "respiratory_rate": 20,
    "oxygen_saturation": 96.5,
    "weight": 72.5,
    "height": 175
  }'
```
```json
{ "uuid": "...", "bmi": 23.67, "temperature": 38.6, ... }
```

---

#### `GET /api/labs/vitals?patient_id=UUID`
**All roles.** List vital signs.

---

#### `GET /api/labs/vitals/patient/{patient_id}`
**All roles.** All vitals for a patient.

---

#### `GET /api/labs/vitals/latest/{patient_id}`
**All roles.** Most recent vital signs for a patient.

```bash
curl "http://localhost:8000/api/labs/vitals/latest/PATIENT-UUID" \
  -H "Authorization: Bearer <physician_token>"
```
**When to use:** Quick check of current vitals on the patient dashboard.

---

#### `GET /api/labs/vitals/{vitals_id}`
**All roles.** Get a specific vital sign record.

---

#### `DELETE /api/labs/vitals/{vitals_id}`
**Nurse, Physician, Admin.** Delete a vital sign record.

---

#### `POST /api/labs/my-vitals`
**Patient only.** Log home vitals (blood pressure, weight, etc.).

---

#### `GET /api/labs/my-vitals`
**Patient only.** Get own vital history.

---

### Clinical Notes (`/api/notes`)

#### `POST /api/notes/?patient_id=UUID`
**Physician, Nurse, Admin.** Create a clinical note. Auto-embeds content into FAISS for semantic RAG retrieval.

```bash
curl -X POST "http://localhost:8000/api/notes/?patient_id=PATIENT-UUID" \
  -H "Authorization: Bearer <physician_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Initial Assessment — Day 1",
    "content": "45-year-old female presenting with 3-day history of productive cough, fever (38.6°C), and right-sided pleuritic chest pain. Examination reveals reduced breath sounds at right base...",
    "note_type": "assessment",
    "note_date": "2025-03-01T11:00:00"
  }'
```
**When to use:** Physician writing a SOAP note or admission assessment — the content is vectorized for future RAG-based diagnostic retrieval.

---

#### `GET /api/notes/?patient_id=UUID&note_type=assessment`
**All roles.** List notes with filters. Patients see only their own.

---

#### `GET /api/notes/my-notes`
**Patient only.** Get own notes (symptoms diary, concerns).

---

#### `POST /api/notes/my-notes`
**Patient only.** Add own note to medical record.

---

#### `GET /api/notes/patient/{patient_id}`
**All roles.** All notes for a specific patient.

---

#### `GET /api/notes/{note_id}`
**All roles.** Get a specific note.

---

#### `PUT /api/notes/{note_id}`
**Physician, Nurse, Admin.** Update a note. Nurses can only edit their own notes.

---

#### `DELETE /api/notes/{note_id}`
**Physician, Admin.** Delete a clinical note.

---

#### `POST /api/notes/{note_id}/summarize?max_length=150`
**Physician, Nurse, Admin.** AI-generated summary of a note using Groq.

```bash
curl -X POST "http://localhost:8000/api/notes/NOTE-UUID/summarize?max_length=100" \
  -H "Authorization: Bearer <physician_token>"
```
```json
{
  "note_id": "NOTE-UUID",
  "original_title": "Initial Assessment — Day 1",
  "original_length": 312,
  "summary": "45F, 3-day productive cough, fever 38.6°C, right-sided pleuritic chest pain, reduced breath sounds right base.",
  "summary_length": 22,
  "model": "mixtral-8x7b-32768 (Groq)"
}
```
**When to use:** Generating handover summaries, referral letter abstracts, or condensing verbose notes for display.

---

### Audit Logs (`/api/audit`)

All audit endpoints are **Admin only** and return paginated results.

#### `GET /api/audit/?page=1&page_size=25&action=login&status=failed`
**Admin only.** Paginated audit log with filters for `user_id`, `patient_id`, `action`, and `status`.

```bash
curl "http://localhost:8000/api/audit/?action=login&status=failed&page=1" \
  -H "Authorization: Bearer <admin_token>"
```
```json
{
  "logs": [
    {
      "uuid": "...",
      "timestamp": "2025-03-01T09:15:00",
      "action": "login",
      "status": "failed",
      "action_details": "Invalid password",
      "ip_address": "192.168.1.10",
      "user": { "username": "john_doe", "role": "patient" }
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 25,
  "total_pages": 1
}
```
**When to use:** Security monitoring, compliance audits, investigating unauthorized access attempts.

---

#### `GET /api/audit/patient/{patient_id}`
**Admin only.** All audit events for a specific patient (who viewed, updated, or generated diagnostics for this patient).

**When to use:** HIPAA/GDPR access log review — "who accessed Jane Doe's record?"

---

#### `GET /api/audit/user/{user_id}`
**Admin only.** All audit events performed by a specific user.

**When to use:** Investigating a staff member's activity pattern.

---

#### `GET /api/audit/actions/summary`
**Admin only.** Count breakdown of all audit action types.

```bash
curl http://localhost:8000/api/audit/actions/summary \
  -H "Authorization: Bearer <admin_token>"
```
```json
{
  "summary": [
    { "action": "view_patient", "count": 1243 },
    { "action": "login", "count": 872 },
    { "action": "upload_document", "count": 156 }
  ]
}
```
**When to use:** Compliance dashboard showing system usage patterns, detecting anomalous activity volumes.

---

#### `GET /api/audit/{log_id}`
**Admin only.** Get a single audit log entry by UUID.

---

## Database Reset

> ⚠️ **Destructive operation — all data will be lost.**

```bash
python reset_database.py
```

Drops all tables and enum types, then recreates the full schema from SQLAlchemy models.

---

## Interactive API Docs

Once the server is running:

- **Swagger UI** → [http://localhost:8000/api/docs](http://localhost:8000/api/docs)  
  Interactive endpoint testing — click "Authorize" to enter your Bearer token.

- **ReDoc** → [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)  
  Clean reference documentation.
