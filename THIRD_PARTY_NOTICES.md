# Third-party notices and licensing record

This file records the direct dependency license metadata for the v0.1 lock files. It is an inventory for project-owner review, not a legal opinion or a substitute for the license texts distributed by the upstream projects. The complete resolved dependency trees are emitted as CycloneDX SBOM artifacts by CI.

## License decision required before public distribution

PyMuPDF 1.27.2.3 is dual licensed under the GNU AGPL-3.0 or an Artifex commercial license. The project owner has not recorded which licensing path applies to this project. The root `LICENSE` is left unchanged; it does not resolve the separate PyMuPDF dependency terms. Before public distribution, the owner must choose and document the applicable path and complete the associated obligations. See the [PyMuPDF licensing documentation](https://pymupdf.readthedocs.io/en/latest/faq/index.html) and [Artifex licensing information](https://artifex.com/licensing).

## Direct Python dependencies

Versions are taken from `backend/requirements.lock` and `backend/requirements-dev.lock`.

| Package | Locked version | Declared license |
|---|---:|---|
| FastAPI | 0.149.0 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyMuPDF | 1.27.2.3 | AGPL-3.0 or Artifex commercial license |
| pdf2docx | 0.5.13 | MIT |
| python-docx | 1.2.0 | MIT |
| Pillow | 13.0.0 | MIT-CMU |
| pytest | 9.1.1 | MIT |
| HTTPX | 0.28.1 | BSD-3-Clause |
| pip-tools | 7.6.2 | BSD-3-Clause |
| pip-audit | 2.10.1 | Apache-2.0 |
| cyclonedx-bom | 7.5.0 | Apache-2.0 |

## Direct frontend dependencies

Versions and license identifiers are taken from `frontend/package-lock.json`.

| Package | Locked version | Declared license |
|---|---:|---|
| @mantine/core | 8.3.18 | MIT |
| @mantine/hooks | 8.3.18 | MIT |
| @tabler/icons-react | 3.49.0 | MIT |
| axios | 1.20.0 | MIT |
| pdfjs-dist | 6.4.299 | Apache-2.0 |
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| react-router-dom | 7.18.4 | MIT |
| @playwright/test | 1.64.1 | Apache-2.0 |
| @testing-library/dom | 10.4.2 | MIT |
| @testing-library/react | 16.3.3 | MIT |
| @types/node | 26.6.5 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @vitejs/plugin-react | 5.2.0 | MIT |
| jsdom | 30.1.3 | MIT |
| TypeScript | 5.9.3 | Apache-2.0 |
| Vite | 7.3.7 | MIT |
| Vitest | 4.1.12 | MIT |

## External system component

Office-to-PDF conversion uses the separately installed LibreOffice application. It is not installed from the Python or npm lock files and is not included in this repository's SBOMs. If redistributed, retain and review the notices supplied by the exact LibreOffice distribution.

## Resolved transitive dependencies

The direct tables above do not enumerate transitive packages. CI creates `python-sbom.json` from the installed locked Python environment and `frontend-sbom.json` from `package-lock.json`; those artifacts contain the resolved dependency inventories and package license metadata for that run. The SBOM run is tied to that CI commit and is not a permanent legal clearance.
