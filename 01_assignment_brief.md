# MLOps Platform Technical Assignment

## Objective

Build a representative MLOps application using a Python backend and Angular GUI. The platform should register, version, approve, deploy, monitor and roll back machine-learning models.

The focus is production-quality engineering rather than sophisticated model training.

## Business Scenario

An industrial organization operates many ML models across plants and environments. The platform must support:

1. Model and version registration
2. Approval and lifecycle promotion
3. Deployment requests and status tracking
4. Monitoring of latency, throughput, quality, drift, error rate and availability
5. Version comparison
6. Deployment event history
7. Retry and rollback
8. Angular-based operational views

## Mandatory Functional Scope

### Model Registry
- Model creation
- Version registration
- Tags and metadata
- Framework and algorithm
- Artifact URI
- Training-data reference
- Approval status
- Lifecycle stage
- Audit timestamps

Suggested stages: `DRAFT`, `VALIDATED`, `APPROVED`, `STAGING`, `PRODUCTION`, `ARCHIVED`.

### Deployment Management
- Deployment request
- Environment selection
- Status tracking
- Deployment history
- Failure simulation
- Retry
- Rollback
- Idempotency

Suggested states: `REQUESTED`, `VALIDATING`, `DEPLOYING`, `SUCCEEDED`, `FAILED`, `ROLLED_BACK`.

### Monitoring
Display:
- Prediction latency
- Throughput
- Error rate
- Quality score
- Drift score
- Availability
- Last successful inference
- Monitoring status

### Angular GUI
- Model inventory
- Model version details
- Deployment view
- Monitoring dashboard
- Event timeline
- Filters and search
- Loading, empty, success and error states
- Responsive layout

### Python Backend
- REST APIs
- Typed requests and responses
- Validation
- Persistence
- Consistent errors
- Structured logging
- Health endpoint
- API documentation
- Unit and integration tests

## Minimum APIs

- `POST /models`
- `GET /models`
- `GET /models/{model_id}`
- `POST /models/{model_id}/versions`
- `GET /models/{model_id}/versions`
- `POST /deployments`
- `GET /deployments`
- `GET /deployments/{deployment_id}`
- `POST /deployments/{deployment_id}/retry`
- `POST /deployments/{deployment_id}/rollback`
- `GET /models/{model_id}/metrics`
- `GET /health`

Equivalent resource-oriented designs are acceptable.

## Acceptance Scenarios

1. Register a model and two versions.
2. Approve one version.
3. Prevent an unapproved version from Production deployment.
4. Deploy an approved version.
5. Show monitoring data in Angular.
6. Retry a failed deployment.
7. Roll back a Production deployment.
8. Handle duplicate deployment requests safely.
9. Surface API failures clearly in the UI.
10. Verify critical workflows through automated tests.

## Suggested Stack

Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL/SQLite, Alembic, Pytest, Angular, TypeScript, Angular Material, RxJS, Docker, Docker Compose and GitHub Actions.

## Time Box

- G13: 8–12 hours
- G12: 12–16 hours

A complete vertical slice is preferred over broad but incomplete scope.
