# ClinSight API Reference

This README documents every API endpoint exposed by the ClinSight FastAPI backend, including request and response shapes, required roles, and query parameters.

Base URL (local dev): http://localhost:8000
Swagger UI: http://localhost:8000/api/docs

Authentication
- Most endpoints require a JWT access token. Provide it using the Authorization header:
  Authorization: Bearer <access_token>
- Role enforcement is done via server-side guards. If a role is not listed, access is denied.

Common response formats
- Most endpoints return JSON. Validation errors return HTTP 422.
- Resource not found returns HTTP 404. Access denied returns HTTP 403.

-------------------------------------------------------------------------------
Root and Health

GET /
- Description: Welcome message and links.
- Auth: none.
- Response:
  {
    "message": "Welcome to ClinSight - Medical Diagnosis Support System",
    "version": "1.0.0",
    "docs": "/api/docs",
    "health": "/health"
  }

GET /health
- Description: Basic service health check.
- Auth: none.
- Response:
  {
    "status": "healthy",
    "app": "ClinSight - Medical Diagnosis Support System",
    "version": "1.0.0"
  }

Static uploads
- Files saved to the uploads directory are served under /uploads.
- Example: GET /uploads/<filename>

-------------------------------------------------------------------------------
Authentication (prefix: /api/auth)

POST /api/auth/register
- Description: Self-register a patient user.
- Auth: none.
- Body (UserCreate):
  {
    "username": "string (3-50)",
    "email": "valid email",
    "full_name": "string (max 100)",
    "password": "string (8-72)",
    "role": "patient"  // optional, must be patient
  }
- Response (UserResponse):
  {
    "uuid": "string",
    "username": "string",
    "email": "string",
    "full_name": "string",
    "role": "patient",
    "is_active": true,
    "created_at": "datetime"
  }

POST /api/auth/bootstrap/admin
- Description: Create the first admin account. Only works if no admin exists.
- Auth: none.
- Body: same as UserCreate, role must be "admin".
- Response: UserResponse.

POST /api/auth/register/staff
- Description: Admin-only staff creation.
- Auth: admin.
- Body: UserCreate with role set to a non-patient role.
- Response: UserResponse.

POST /api/auth/login
- Description: JSON login. Returns access token.
- Auth: none.
- Body (UserLogin):
  {
    "username": "string",
    "password": "string"
  }
- Response (Token):
  {
    "access_token": "string",
    "token_type": "bearer",
    "user": { ...UserResponse }
  }

GET /api/auth/users
- Description: List all users.
- Auth: admin.
- Response: array of UserResponse.

GET /api/auth/users/{user_id}
- Description: Get a user by UUID.
- Auth: admin.
- Response: UserResponse.

-------------------------------------------------------------------------------
Patients (prefix: /api/patients)

GET /api/patients/my-record
- Description: Patient portal - get the current user's own record.
- Auth: patient.
- Response: PatientResponse.

POST /api/patients/link-my-record
- Description: Patient portal - link account to an existing record by MRN.
- Auth: patient.
- Query params:
  - mrn: string
- Response:
  { "message": "Patient record successfully linked", "patient_id": "uuid", "mrn": "MRN-12345" }

POST /api/patients
- Description: Create a patient record.
- Auth: intake, admin.
- Body (PatientCreate):
  {
    "full_name": "string",
    "date_of_birth": "YYYY-MM-DD",
    "gender": "string",
    "phone": "string|null",
    "address": "string|null",
    "email": "string|null",
    "visit_type": "string|null",
    "chief_complaint": "string|null",
    "visit_date": "datetime|null"
  }
- Response: PatientResponse.

GET /api/patients
- Description: List patients with pagination and filters.
- Auth: intake, nurse, radiologist, physician, admin, compliance.
- Query params:
  - page: int (default 1)
  - page_size: int (default 10, max 100)
  - search: string (matches name, MRN, or email)
  - status_filter: string
- Response (PatientListResponse):
  { "patients": [PatientResponse], "total": int, "page": int, "page_size": int }

GET /api/patients/{patient_id}
- Description: Get patient details by UUID.
- Auth: intake, nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: PatientResponse.

PUT /api/patients/{patient_id}
- Description: Update a patient.
- Auth: intake, nurse, physician, admin.
- Body (PatientUpdate):
  {
    "full_name": "string|null",
    "phone": "string|null",
    "address": "string|null",
    "email": "string|null",
    "status": "string|null",
    "chief_complaint": "string|null",
    "visit_type": "string|null"
  }
- Response: PatientResponse.

DELETE /api/patients/{patient_id}
- Description: Delete a patient record.
- Auth: admin.
- Response: 204 No Content.

-------------------------------------------------------------------------------
Documents (prefix: /api/documents)

POST /api/documents/upload
- Description: Upload a document for a patient.
- Auth: intake, nurse, physician, admin, patient (patients can only upload to their own record).
- Body: multipart/form-data
  - file: file
  - patient_id: uuid
  - document_type: string (must match a DocumentType enum value)
  - notes: string (optional)
- Response (DocumentUploadResponse):
  {
    "uuid": "string",
    "filename": "string",
    "document_type": "string",
    "file_size": int,
    "patient_id": "uuid",
    "upload_date": "datetime"
  }

POST /api/documents/bulk-upload
- Description: Upload multiple documents for one patient.
- Auth: intake, admin.
- Body: multipart/form-data
  - files: list of files
  - patient_id: uuid
  - document_types: comma-separated list matching each file
- Response: array of DocumentUploadResponse.

GET /api/documents
- Description: List documents.
- Auth: intake, nurse, radiologist, physician, admin, compliance, patient (self only).
- Query params:
  - patient_id: uuid (optional)
  - document_type: string (optional)
- Response: array of DocumentResponse.

GET /api/documents/my-documents
- Description: Patient portal - list the patient's own documents.
- Auth: patient.
- Query params:
  - document_type: string (optional)
- Response: array of DocumentResponse.

GET /api/documents/{document_id}
- Description: Get document metadata.
- Auth: intake, nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: DocumentResponse.

GET /api/documents/{document_id}/download
- Description: Download the physical file.
- Auth: intake, nurse, radiologist, physician, admin, patient (self only).
- Response: file download.

DELETE /api/documents/{document_id}
- Description: Delete a document and its physical file.
- Auth: intake, physician, admin.
- Response: 204 No Content.

POST /api/documents/my-documents/upload
- Description: Patient portal - upload a document to own record.
- Auth: patient.
- Body: multipart/form-data
  - file: file
  - document_type: string
  - notes: string (optional)
- Response: DocumentUploadResponse.

-------------------------------------------------------------------------------
Clinical Notes (prefix: /api/notes)

POST /api/notes
- Description: Create a clinical note.
- Auth: physician, nurse, admin.
- Query params:
  - patient_id: uuid
- Body (ClinicalNoteCreate):
  {
    "title": "string",
    "content": "string",
    "note_type": "string|null",
    "note_date": "datetime"
  }
- Response: ClinicalNoteResponse.

GET /api/notes
- Description: List notes with optional filters.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Query params:
  - patient_id: uuid (optional)
  - note_type: string (optional)
- Response: array of ClinicalNoteResponse.

GET /api/notes/patient/{patient_id}
- Description: Get all notes for a patient.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: array of ClinicalNoteResponse.

GET /api/notes/{note_id}
- Description: Get a specific note by UUID.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: ClinicalNoteResponse.

PUT /api/notes/{note_id}
- Description: Update a clinical note.
- Auth: physician, nurse, admin (nurses can edit only their own notes).
- Body: ClinicalNoteCreate.
- Response: ClinicalNoteResponse.

DELETE /api/notes/{note_id}
- Description: Delete a note.
- Auth: physician, admin.
- Response: 204 No Content.

POST /api/notes/{note_id}/summarize
- Description: Generate an AI summary for a note.
- Auth: physician, nurse, admin.
- Query params:
  - max_length: int (50-500, default 200)
- Response:
  {
    "note_id": "uuid",
    "original_title": "string",
    "original_length": int,
    "summary": "string",
    "summary_length": int,
    "model": "mixtral-8x7b-32768 (Groq)"
  }

POST /api/notes/my-notes
- Description: Patient portal - create a patient note.
- Auth: patient.
- Body: ClinicalNoteCreate.
- Response: ClinicalNoteResponse.

GET /api/notes/my-notes
- Description: Patient portal - list patient's own notes.
- Auth: patient.
- Query params:
  - note_type: string (optional)
- Response: array of ClinicalNoteResponse.

-------------------------------------------------------------------------------
Labs and Vitals (prefix: /api/labs)

Lab results

POST /api/labs/labs
- Description: Create a lab result.
- Auth: nurse, physician, admin.
- Query params:
  - patient_id: uuid
- Body (LabResultCreate):
  {
    "test_name": "string",
    "test_value": number,
    "unit": "string",
    "test_date": "datetime",
    "reference_range_low": number|null,
    "reference_range_high": number|null,
    "is_abnormal": "Normal|High|Low|null",
    "notes": "string|null"
  }
- Response: LabResultResponse.

GET /api/labs/labs
- Description: List lab results.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Query params:
  - patient_id: uuid (optional)
  - test_name: string (optional)
- Response: array of LabResultResponse.

GET /api/labs/labs/patient/{patient_id}
- Description: Get labs for a specific patient.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: array of LabResultResponse.

GET /api/labs/labs/{lab_id}
- Description: Get a lab result by UUID.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: LabResultResponse.

PUT /api/labs/labs/{lab_id}
- Description: Update a lab result.
- Auth: nurse, physician, admin.
- Body: LabResultCreate.
- Response: LabResultResponse.

DELETE /api/labs/labs/{lab_id}
- Description: Delete a lab result.
- Auth: physician, admin.
- Response: 204 No Content.

POST /api/labs/my-labs
- Description: Patient portal - add an external lab result.
- Auth: patient.
- Body: LabResultCreate.
- Response: LabResultResponse.

GET /api/labs/my-labs
- Description: Patient portal - list own lab results.
- Auth: patient.
- Response: array of LabResultResponse.

Vital signs

POST /api/labs/vitals
- Description: Create a vital sign record (BMI auto-calculated).
- Auth: nurse, physician, admin.
- Query params:
  - patient_id: uuid
- Body (VitalSignCreate):
  {
    "measurement_date": "datetime",
    "temperature": number|null,
    "temperature_unit": "C|F",
    "systolic_bp": number|null,
    "diastolic_bp": number|null,
    "heart_rate": number|null,
    "respiratory_rate": number|null,
    "oxygen_saturation": number|null,
    "weight": number|null,
    "height": number|null,
    "notes": "string|null"
  }
- Response: VitalSignResponse.

GET /api/labs/vitals
- Description: List vital signs.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Query params:
  - patient_id: uuid (optional)
- Response: array of VitalSignResponse.

GET /api/labs/vitals/patient/{patient_id}
- Description: Get vitals for a specific patient.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: array of VitalSignResponse.

GET /api/labs/vitals/latest/{patient_id}
- Description: Get latest vitals for a patient.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: VitalSignResponse.

DELETE /api/labs/vitals/{vitals_id}
- Description: Delete a vital sign record.
- Auth: nurse, physician, admin.
- Response: 204 No Content.

POST /api/labs/my-vitals
- Description: Patient portal - add home vitals.
- Auth: patient.
- Body: VitalSignCreate.
- Response: VitalSignResponse.

GET /api/labs/my-vitals
- Description: Patient portal - list home vitals.
- Auth: patient.
- Response: array of VitalSignResponse.

-------------------------------------------------------------------------------
Imaging Studies (prefix: /api/imaging)

POST /api/imaging
- Description: Create an imaging study.
- Auth: nurse, radiologist, physician, admin.
- Query params:
  - patient_id: uuid
- Body (ImagingStudyCreate):
  {
    "study_date": "datetime",
    "modality": "X-ray|CT|MRI|Ultrasound|PET",
    "body_part": "string",
    "description": "string|null"
  }
- Response: ImagingStudyResponse.

GET /api/imaging
- Description: List imaging studies.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Query params:
  - patient_id: uuid (optional)
  - modality: string (optional)
  - status_filter: string (optional)
- Response: array of ImagingStudyResponse.

GET /api/imaging/patient/{patient_id}
- Description: Get imaging studies for a patient.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: array of ImagingStudyResponse.

GET /api/imaging/{study_id}
- Description: Get imaging study details.
- Auth: nurse, radiologist, physician, admin, compliance, patient (self only).
- Response: ImagingStudyResponse.

PUT /api/imaging/{study_id}
- Description: Update an imaging study.
- Auth: radiologist, physician, admin.
- Body (ImagingStudyUpdate):
  {
    "findings": "string|null",
    "impression": "string|null",
    "status": "string|null"
  }
- Response: ImagingStudyResponse.

PUT /api/imaging/{study_id}/interpret
- Description: Add radiologist interpretation.
- Auth: radiologist, admin.
- Body: ImagingStudyUpdate (findings, impression, status).
- Response: ImagingStudyResponse.

DELETE /api/imaging/{study_id}
- Description: Delete an imaging study.
- Auth: physician, admin.
- Response: 204 No Content.

-------------------------------------------------------------------------------
AI Diagnostic Support (prefix: /api/diagnostic)

POST /api/diagnostic/generate
- Description: Generate an AI diagnostic report.
- Auth: physician, admin.
- Body (DiagnosticQuery):
  {
    "patient_id": "uuid",
    "query": "string|null",
    "clinical_notes": "string|null",
    "include_images": true
  }
- Response (DiagnosticReportResponse):
  {
    "uuid": "string",
    "title": "string|null",
    "summary": "string",
    "suggested_conditions": [ { "condition": "string", "confidence": number } ] | null,
    "evidence_summary": "string|null",
    "citations": [ { "source": "string", "snippet": "string" } ] | null,
    "created_at": "datetime"
  }

GET /api/diagnostic/reports/{patient_id}
- Description: List diagnostic reports for a patient.
- Auth: physician, admin, patient (self only).
- Response: array of DiagnosticReportResponse.

GET /api/diagnostic/reports/detail/{report_id}
- Description: Get a diagnostic report by UUID.
- Auth: physician, admin, patient (self only).
- Response: DiagnosticReportResponse.

POST /api/diagnostic/analyze-image
- Description: Analyze a single medical image using Gemini Vision.
- Auth: physician, radiologist, admin.
- Query params:
  - image_path: string (server-accessible file path)
  - query: string (optional)
- Response:
  { "image_path": "string", "analysis": "string", "model": "gemini-1.5-pro-vision" }

POST /api/diagnostic/summarize-note
- Description: Summarize a note using Groq.
- Auth: physician, nurse, admin.
- Query params:
  - note_text: string
  - max_length: int (default 200)
- Response:
  { "original_length": int, "summary_length": int, "summary": "string", "model": "mixtral-8x7b-32768" }

POST /api/diagnostic/extract-entities
- Description: Extract medical entities from text using Groq.
- Auth: physician, nurse, admin.
- Query params:
  - text: string
- Response:
  { "text_length": int, "entities": [ ... ], "model": "mixtral-8x7b-32768" }

GET /api/diagnostic/capabilities
- Description: Describe available AI capabilities.
- Auth: physician, admin.
- Response: JSON describing models and features.

-------------------------------------------------------------------------------
Audit Logs (prefix: /api/audit)

GET /api/audit
- Description: Paginated audit log list with filters.
- Auth: admin, compliance.
- Query params:
  - page: int (default 1)
  - page_size: int (default 25, max 100)
  - user_id: uuid (optional)
  - patient_id: uuid (optional)
  - action: string (optional)
  - status: string (optional)
- Response (AuditLogListResponse):
  {
    "logs": [AuditLogResponse],
    "total": int,
    "page": int,
    "page_size": int,
    "total_pages": int
  }

GET /api/audit/patient/{patient_id}
- Description: Audit logs for a specific patient.
- Auth: admin, compliance.
- Query params:
  - page: int (default 1)
  - page_size: int (default 25)
- Response: AuditLogListResponse.

GET /api/audit/user/{user_id}
- Description: Audit logs for a specific user.
- Auth: admin, compliance.
- Query params:
  - page: int (default 1)
  - page_size: int (default 25)
- Response: AuditLogListResponse.

GET /api/audit/{log_id}
- Description: Get a single audit log entry by UUID.
- Auth: admin, compliance.
- Response: AuditLogResponse.

GET /api/audit/actions/summary
- Description: Count of audit actions grouped by action type.
- Auth: admin, compliance.
- Response:
  { "summary": [ { "action": "string", "count": int } ] }

## 🔍 Monitoring & Debugging

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f postgres
docker-compose logs -f neo4j
docker-compose logs -f milvus
```

### Check Resource Usage
```bash
docker stats
```

### Database Access

**PostgreSQL**:
```bash
docker-compose exec postgres psql -U medical_user -d medical_db
```

**Neo4j**:
- Browser: http://localhost:7474
- Credentials: neo4j / <NEO4J_PASSWORD>

**Milvus**:
```bash
docker-compose exec backend python
>>> from pymilvus import connections, Collection
>>> connections.connect(host="milvus", port="19530")
>>> collection = Collection("medical_embeddings")
>>> collection.num_entities
```

## 📊 Data Processing Pipeline

### Phase 1: File Upload & Preprocessing

```
User uploads file → FastAPI validates → Save to volume
                                      ↓
                        PreprocessingService processes:
                        - DICOM: Extract metadata, normalize pixels
                        - Images: Analyze, generate descriptions
                        - Text: NLP entity extraction, chunking
                                      ↓
                        Store metadata in PostgreSQL
```

### Phase 2: Vectorization

```
Processed file → BioBERT embedding generation
                           ↓
              Batch processing (32 chunks/batch)
                           ↓
              Store in Milvus with metadata
                           ↓
              Update file.vector_id in PostgreSQL
```

### Phase 3: Hybrid Retrieval

```
User query → Generate query embedding
                     ↓
         ┌───────────┴────────────┐
         ▼                        ▼
   Vector Search            Graph Search
   (Milvus COSINE)         (Neo4j Cypher)
         │                        │
         └───────────┬────────────┘
                     ▼
         Reciprocal Rank Fusion
                     ▼
         Combined context (top 10 sources)
```

### Phase 4: Report Generation

```
Context + Images → Build structured prompt
                           ↓
                  Google Gemini API
                  (gemini-pro-vision)
                           ↓
              Generated diagnostic report
                           ↓
         Store in PostgreSQL with sources
```

## 🔒 Security Best Practices

1. **Environment Variables**: Never commit `.env` to version control
2. **Password Hashing**: Bcrypt with automatic salt generation
3. **JWT Tokens**: 30-minute expiration, HS256 algorithm
4. **Input Validation**: Pydantic schemas for all API inputs
5. **File Upload**: Extension whitelist, size limits
6. **User Isolation**: All queries filtered by user_id
7. **SQL Injection**: SQLAlchemy ORM prevents injection
8. **Cypher Injection**: Parameterized Neo4j queries

## 🐛 Troubleshooting

### Services won't start
```bash
# Check Docker resources
docker system df

# Clean up if needed
docker system prune -a

# Rebuild from scratch
docker-compose down -v
./build.sh
./run.sh
```

### Backend connection errors
```bash
# Check network
docker network ls
docker network inspect medical-ai-diagnostic_medical_network

# Restart backend
docker-compose restart backend
```

### Milvus connection issues
```bash
# Check Milvus health
curl http://localhost:9091/healthz

# Restart Milvus stack
docker-compose restart milvus-etcd milvus-minio milvus
```

### Out of memory errors
```bash
# Increase Docker Desktop memory allocation
# Settings → Resources → Memory (recommended: 8GB+)

# Check container memory usage
docker stats
```

## 🚀 Production Deployment Considerations

### Scaling

1. **Horizontal Scaling**: Use Kubernetes for multi-replica deployment
2. **Database Replication**: PostgreSQL primary-replica setup
3. **Vector DB Clustering**: Milvus distributed mode
4. **Load Balancing**: Nginx/Traefik for API requests
5. **Async Processing**: Celery + Redis for background tasks

### Performance Optimization

1. **Caching**: Redis for frequent queries
2. **CDN**: Static file serving
3. **Connection Pooling**: Already configured (10-30 connections)
4. **Batch Processing**: GPU batching for embeddings
5. **Index Tuning**: Milvus IVF_FLAT → HNSW for production

### Monitoring

1. **Metrics**: Prometheus + Grafana
2. **Logging**: ELK Stack (Elasticsearch, Logstash, Kibana)
3. **Tracing**: Jaeger for distributed tracing
4. **Alerting**: PagerDuty/Opsgenie integration

### Security Hardening

1. **HTTPS**: TLS certificates (Let's Encrypt)
2. **Rate Limiting**: Slowapi middleware
3. **API Gateway**: Kong/Tyk for advanced security
4. **Secrets Management**: Vault/AWS Secrets Manager
5. **Network Policies**: Kubernetes NetworkPolicies

## 📚 Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Milvus Documentation](https://milvus.io/docs)
- [Neo4j Documentation](https://neo4j.com/docs/)
- [Google Gemini API](https://ai.google.dev/docs)
- [BioBERT Paper](https://arxiv.org/abs/1901.08746)
- [SNOMED CT](https://www.snomed.org/)

## 📝 License

This project is for educational and research purposes. Ensure compliance with:
- HIPAA (if handling real patient data)
- GDPR (for EU users)
- Medical device regulations (if deployed clinically)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Submit pull request with detailed description

## 📧 Support

For issues and questions:
- GitHub Issues: [Link to repository]
- Documentation: [Link to docs]
- Email: support@example.com

---

**Built with ❤️ for advancing AI-powered medical diagnostics**