import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME = os.getenv("APP_NAME", "CodeIntelligence")
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    # Persistence modes:
    #   sqlite   -> local file only (recommended for hackathon/offline)
    #   postgres -> PostgreSQL only
    #   dual     -> SQLite primary + best-effort PostgreSQL mirror
    DATABASE_MODE = os.getenv("DATABASE_MODE", "sqlite").strip().lower()
    LOCAL_DATABASE_URL = os.getenv(
        "LOCAL_DATABASE_URL",
        "sqlite:///./codeintelligence.db"
    )
    POSTGRES_DATABASE_URL = os.getenv(
        "POSTGRES_DATABASE_URL",
        os.getenv("DATABASE_URL")
    )

    # Backward compatibility for existing installations that only have DATABASE_URL.
    DATABASE_URL = os.getenv("DATABASE_URL")

    JAVA_PROJECT_PATH = os.getenv("JAVA_PROJECT_PATH")
    AUTO_PROJECT_INITIALIZATION = os.getenv("AUTO_PROJECT_INITIALIZATION", "true").lower() == "true"
    NEO4J_ENABLED = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
    NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

    # Jira requirement understanding only. Java source code is never sent to the LLM.
    JIRA_LLM_ENABLED = os.getenv("JIRA_LLM_ENABLED", "true").lower() == "true"
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

    # Local/private provider (default).
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    # Optional hosted provider fallback.
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Live Jira connectivity. Authentication is intentionally minimal for now.
    JIRA_LIVE_ENABLED = os.getenv("JIRA_LIVE_ENABLED", "false").lower() == "true"
    JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
    JIRA_EMAIL = os.getenv("JIRA_EMAIL", "").strip()
    JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "").strip()

    # Phase 8 Enterprise Hybrid RAG. pgvector uses the existing PostgreSQL instance.
    PHASE8_VECTOR_BACKEND = os.getenv("PHASE8_VECTOR_BACKEND", "pgvector").strip().lower()
    PHASE8_EMBEDDING_MODEL = os.getenv("PHASE8_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


settings = Settings()
