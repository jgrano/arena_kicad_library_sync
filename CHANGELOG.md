# Changelog

## [1.0.0] - Unreleased

### Added
- Bidirectional sync between Arena PLM and KiCad libraries
- KiCad HTTP lib support via FastAPI server
- KiCad DB lib support via SQLite + .kicad_dbl generation
- Conflict detection and resolution UI (wxPython)
- FastAPI middleware server for shared/Docker deployment
- Cross-platform credential storage via keyring
- Configurable field mappings (Arena <-> KiCad)
- Delta sync using Arena modification timestamps
- KiCad PCM-compatible packaging
- Docker + docker-compose for server deployment
- Full pytest test suite (85%+ coverage)
