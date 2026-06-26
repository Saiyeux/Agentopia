# Agentopia

Agentopia is an AI-driven autonomous character world simulator. SQLite is the single source of truth: LLMs propose semantic intent and deltas, while the engine validates and persists state.

## Project Structure

```text
.
├── backend/              # Python API, engine orchestration, SQLite access
│   ├── app/              # Application modules
│   ├── data/             # Local runtime database files, ignored by Git
│   ├── requirements.txt
│   └── run.py
├── frontend/             # Vite + React observer UI
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── docs/                 # Architecture and implementation design notes
├── AGENTS.md             # Codex working instructions
└── claude.md             # Claude Code working instructions
```

## Development

Backend:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python run.py
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Notes

- Keep generated databases under `backend/data/`; they are local runtime state and are ignored by Git.
- Keep design changes synchronized across the files in `docs/`.
