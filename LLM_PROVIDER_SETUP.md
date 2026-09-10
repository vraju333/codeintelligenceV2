# LLM Provider Setup

CodeIntelligence supports two interchangeable providers for **Jira requirement understanding**:

- `ollama` — local inference; Jira requirement text stays on the local machine/network.
- `openai` — hosted inference through the OpenAI API.

Java source code, RAG documents, endpoint flows, and scenarios are not sent to either provider by `RequirementLlmService`; only the Jira requirement text is passed to the selected provider.

## Option A: Ollama (default)

Install Ollama, then download the model once:

```powershell
ollama pull llama3.1:8b
ollama list
```

Configure `.env`:

```env
JIRA_LLM_ENABLED=true
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

No OpenAI key is needed.

## Option B: OpenAI

Configure `.env`:

```env
JIRA_LLM_ENABLED=true
LLM_PROVIDER=openai
OPENAI_API_KEY=your_actual_key
OPENAI_MODEL=gpt-4o-mini
```

Restart CodeIntelligence after changing providers.

## Run

```powershell
python -m uvicorn main:app --reload
```

## Architecture

```text
Jira requirement
      |
RequirementLlmService
      |
   provider
   /     \
Ollama  OpenAI
   \     /
structured requirement
      |
local RAG + Java scanner + flow/scenario analysis
```
