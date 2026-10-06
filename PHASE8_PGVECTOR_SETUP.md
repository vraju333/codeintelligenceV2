# Phase 8 - PostgreSQL + pgvector

Phase 8 no longer uses FAISS as its production vector store. It uses the existing PostgreSQL server with the pgvector extension, BM25 lexical retrieval, metadata filtering and deterministic reranking.

## Prerequisite

The PostgreSQL server must have the `vector` extension installed. The application executes `CREATE EXTENSION IF NOT EXISTS vector` automatically, but the extension binaries must already be available in PostgreSQL.

Configure `.env`:

```env
POSTGRES_DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/codeintelligence
PHASE8_VECTOR_BACKEND=pgvector
PHASE8_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

`DATABASE_MODE` may remain `sqlite`, `dual`, or `postgres`; Phase 8 itself always uses `POSTGRES_DATABASE_URL` for pgvector.

## Start and verify

1. Start CodeIntelligence.
2. `GET /api/enterprise-rag/status`
3. `POST /api/enterprise-rag/rebuild`
4. `POST /api/enterprise-rag/search` with `{ "query": "GPA promotion", "top_k": 10 }`.

Expected retrieval strategy: `HYBRID_PGVECTOR_BM25_METADATA_RERANK` and `faiss_used: false`.

The older FAISS-based endpoints remain in the project for backward compatibility during migration, but Phase 8 does not call them.
