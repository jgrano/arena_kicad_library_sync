# Arena KiCad Library Sync

KiCad action plugin for bidirectional library sync with Arena PLM.

Supports HTTP lib, DB lib, and server middleware deployment modes.

## Features

- **Bidirectional sync** — Pull components from Arena into KiCad libraries, push local changes back
- **HTTP lib** — Serve Arena components to KiCad via the HTTP library protocol
- **DB lib** — Maintain a local SQLite database as a KiCad database library
- **Conflict resolution** — Detect and resolve conflicts with a visual diff UI
- **Server mode** — Deploy as a FastAPI middleware on shared infrastructure (Docker)
- **Local mode** — Run entirely within KiCad with zero server infrastructure

## Requirements

- KiCad 8.0+
- Python 3.10+
- Arena PLM account with API access

## Installation

### KiCad Plugin Manager (PCM)

Download the latest release ZIP and install via KiCad's Plugin and Content Manager.

### Manual Install

```bash
./scripts/install_local.sh
```

### Server Deployment (Docker)

```bash
cd docker
cp .env.example .env
# Edit .env with your Arena credentials
docker-compose up -d
```

## Configuration

On first run, the plugin opens a settings dialog to configure:

1. Arena API credentials (stored in system keyring)
2. Deployment mode (local / server / both)
3. Sync direction and conflict strategy
4. Field mappings between Arena and KiCad

## License

MIT — see [LICENSE](LICENSE).
