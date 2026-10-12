# GitHub Submission Guidelines

## Repository
Recommended name: `mlops-platform-technical-assignment`.

The repository may be public or private with evaluator access.

## Recommended Structure

```text
.
├── README.md
├── docs/
│   ├── architecture.md
│   ├── architecture-diagram.png
│   ├── api-design.md
│   ├── test-strategy.md
│   ├── known-limitations.md
│   └── adr/
├── backend/
├── frontend/
├── data/
├── scripts/
├── .github/workflows/ci.yml
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── Makefile
```

## README Must Include
- Role level: G12 or G13
- Problem statement
- Architecture summary
- Technology stack
- Setup and run instructions
- Test commands
- API documentation location
- Screenshots
- Sample workflows
- Known limitations
- Future improvements

## Git Expectations
Use meaningful commits and preferably at least one pull request.

## Packaging
Preferred command:

```text
docker compose up --build
```

The setup should start backend, frontend, database and worker/queue when used.

## CI
Run Python lint/tests, Angular lint/tests, build validation and practical dependency checks.

## Final Checklist
- Repository accessible
- Setup works cleanly
- No secrets committed
- Tests pass
- Architecture diagram included
- Sample data available
- Screenshots included
- Known limitations documented
- Role level clearly stated
