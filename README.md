# Medical AI Diagnostic Reasoning System

A production-ready, locally-deployed AI-powered diagnostic reasoning system combining FastAPI, PostgreSQL, Neo4j, Milvus, and Google Gemini for multi-modal medical data analysis.

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Auth Service │  │ Preprocessing│  │   Gemini AI  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└───────────┬────────────────┬────────────────┬───────────────┘
            │                │                │
   ┌────────▼────────┐  ┌───▼──────┐  ┌─────▼──────┐
   │   PostgreSQL    │  │  Neo4j   │  │   Milvus   │
   │  (Relational)   │  │ (Graph)  │  │  (Vector)  │
   └─────────────────┘  └──────────┘  └────────────┘
```

### Technology Stack

- **Backend**: FastAPI 0.104+ (Python 3.11)
- **Databases**:
  - PostgreSQL 15 (relational data)
  - Neo4j 5.13 (SNOMED CT knowledge graph)
  - Milvus 2.3 (vector embeddings)
- **AI/ML**:
  - BioBERT (embedding generation)
  - Google Gemini Pro Vision (multi-modal reasoning)
  - spaCy + scispaCy (medical NLP)
- **Infrastructure**: Docker Compose, Docker Desktop

## 🚀 Quick Start

### Prerequisites

- Docker Desktop (with at least 8GB RAM allocated)
- Google Cloud API key with Gemini API enabled
- SNOMED CT RF2 files (optional, for knowledge graph)

### Installation

1. **Clone and Setup**:
```bash
git clone <repository>
cd medical-ai-diagnostic
```

2. **Configure Environment**:
```bash
cp .env.example .env
nano .env  # Edit with your credentials
```

Required environment variables:
```bash
# Security
SECRET_KEY=<generate with: openssl rand -hex 32>
GOOGLE_API_KEY=<your-google-api-key>

# Database passwords
POSTGRES_PASSWORD=<strong-password>
NEO4J_PASSWORD=<strong-password>
```

3. **Build and Start**:
```bash
chmod +x build.sh run.sh
./build.sh  # One-time build
./run.sh    # Start all services
```

4. **Verify Installation**:
```bash
# Check all services are running
docker-compose ps

# Check backend health
curl http://localhost:8000/health
```

## 📋 Service URLs

| Service | URL | Description |
|---------|-----|-------------|
| API Documentation | http://localhost:8000/api/docs | Interactive Swagger UI |
| Health Check | http://localhost:8000/health | Service status |
| Neo4j Browser | http://localhost:7474 | Graph database UI |
| PostgreSQL | localhost:5432 | Relational database |
| Milvus | localhost:19530 | Vector database |

## 🔧 Configuration

### Database Configuration

**PostgreSQL** stores:
- User accounts and authentication
- File metadata and processing status
- Generated reports and source attribution

**Neo4j** stores:
- SNOMED CT ontology (optional)
- Medical concept relationships
- Hierarchical disease classifications

**Milvus** stores:
- BioBERT embeddings (768-dim vectors)
- Text chunk metadata
- Similarity search indexes

### Model Configuration

**BioBERT** (`dmis-lab/biobert-v1.1`):
- Pre-trained on PubMed abstracts
- 768-dimensional embeddings
- Optimized for medical text

**Google Gemini Pro Vision**:
- Multi-modal analysis (text + images)
- Temperature: 0.3 (factual responses)
- Max tokens: 2048

## 🔐 API Usage

### Authentication

1. **Register a user**:
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "doctor1",
    "email": "doctor1@hospital.com",
    "password": "SecurePass123!",
    "full_name": "Dr. Smith"
  }'
```

2. **Login and get token**:
```bash
curl -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=doctor1&password=SecurePass123!"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### File Upload

```bash
TOKEN="your_access_token_here"

curl -X POST http://localhost:8000/api/files/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@chest_xray.dcm"
```

### Generate Diagnostic Report

```bash
curl -X POST http://localhost:8000/api/reasoning/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Analyze chest X-ray findings and provide differential diagnosis for respiratory symptoms",
    "file_ids": [1, 2],
    "include_images": true,
    "max_sources": 10
  }'
```

## 📁 Project Structure

```
medical-ai-diagnostic/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI application
│   │   ├── config.py            # Configuration management
│   │   ├── models/              # SQLAlchemy ORM models
│   │   │   ├── user.py
│   │   │   ├── file.py
│   │   │   └── report.py
│   │   ├── schemas/             # Pydantic schemas
│   │   │   ├── auth.py
│   │   │   ├── file.py
│   │   │   └── report.py
│   │   ├── routers/             # API endpoints
│   │   │   ├── auth.py
│   │   │   ├── files.py
│   │   │   └── reasoning.py
│   │   ├── services/            # Business logic
│   │   │   ├── preprocessing.py # Multi-modal processing
│   │   │   ├── vectorization.py # Embedding generation
│   │   │   ├── retrieval.py     # Hybrid RAG
│   │   │   └── gemini_service.py # Gemini integration
│   │   ├── database/            # Database connections
│   │   │   ├── postgres.py
│   │   │   ├── neo4j_db.py
│   │   │   └── vector_db.py
│   │   └── utils/               # Utilities
│   │       ├── auth.py          # JWT handling
│   │       ├── security.py      # Password hashing
│   │       └── logging_config.py
│   ├── scripts/
│   │   └── ingest_snomed.py     # SNOMED CT ingestion
│   ├── tests/
│   │   └── test_auth.py
│   ├── Dockerfile
│   └── requirements.txt
├── data/                         # Persistent data (git-ignored)
│   ├── postgres/
│   ├── neo4j/
│   ├── milvus/
│   └── uploads/
├── snomed/                       # SNOMED CT RF2 files
├── docker-compose.yml
├── .env
├── build.sh
├── run.sh
└── README.md
```

## 🧪 Testing

### Run Unit Tests
```bash
# From backend directory
docker-compose exec backend pytest tests/ -m unit -v
```

### Run Integration Tests
```bash
docker-compose exec backend pytest tests/ -m integration -v
```

### Security Scanning
```bash
# Install OWASP ZAP
# Run against local API
docker run -t owasp/zap2docker-stable zap-baseline.py \
  -t http://host.docker.internal:8000
```

### Load Testing
```bash
# Install Locust
pip install locust

# Run load tests
locust -f tests/locustfile.py --host=http://localhost:8000
```

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