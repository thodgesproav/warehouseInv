# Installation

## Docker (recommended)

Use the [single-image deployment guide](DOCKER_DEPLOYMENT.md). Compose and a `.env` file are not required. A fresh volume opens a protected setup wizard to create the Superadmin and configure HTTP URLs. The release image defaults to Power Automate mode and does not include the repository workbook or development data.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd frontend && npm install && npm run build && cd ..
python scripts/prepare_local.py
PYTHONPATH=backend uvicorn app.main:app --reload
```

The local default creates `data/runtime/Warehouse Consumables.xlsx`; it does not edit the repository source workbook. On an empty database, read the first-run code from `data/setup-token` after starting the server and complete the wizard. Existing databases retain their users and configuration.

## Upgrade

Back up the whole data volume, stop the old container, then recreate it from the new image with the same volume. See the deployment guide for details. Rebuilding the image does not remove the named volume.
