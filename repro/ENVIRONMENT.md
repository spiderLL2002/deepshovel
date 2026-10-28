# Versions and environment

- Public artifact release: `www2027-artifact-v1.0.0`.
- Full Web package: 1.1.1; full Compiler package: 0.3.1.
- Original source snapshot and file correspondence: `docs/source-snapshot.json`.
- Core replay: Python standard library, reference Python 3.12.13.
- Extended Python replay: complete pins in `repro/requirements.lock`.
- JavaScript consumer: jsonld.js 9.0.0 and `artifact/rdfjs/package-lock.json`.
- RDFLib: 7.6.0.
- Web dependencies: `package-lock.json`; Node 22.13 or newer.
- Offline fixtures use no model API or original/private checkout. Model-generated results are stored artifacts, not regenerated predictions.

Installation needs Python/npm package-index access for the extended tier and full application. After Python installation, the production contract fixtures use only the bundled modules and fixture inputs. The JavaScript consumer installation is isolated in the run directory. The core tier has no third-party install.

Every publication file is listed in `repro/files.json`, except that self-referential manifest and generated reports. The release also supplies downloadable archive checksums. New reproduction output goes outside the tracked inputs.
