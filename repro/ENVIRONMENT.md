# Versions and environment

- Public artifact release: `www2027-artifact-v1.1.0` (software citation version `1.1.0`).
- Demo manuscript: [`paper/main.pdf`](../paper/main.pdf).
- Full Web package: 1.1.1; full Compiler package: 0.3.1.
- Original source snapshot and file correspondence: `docs/source-snapshot.json`.
- Core replay: Python standard library, reference Python 3.12.13.
- Extended Python replay: complete pins in `repro/requirements.lock`.
- JavaScript consumer: jsonld.js 9.0.0 and `artifact/rdfjs/package-lock.json`.
- RDFLib: 7.6.0.
- Web dependencies: `package-lock.json`; Node 22.13 or newer.
- Offline fixtures use no model API or original/private checkout. Model-generated results are stored artifacts, not regenerated predictions.

## Reasoning and research evidence

The new Ubuntu CI job uses **Node.js 22.13.0** and **Python 3.12.13**. The small API-regression additions (FastAPI, multipart parsing and pytest with their dependencies) are pinned in `repro/requirements-comparison.lock`; no GPU or Qdrant installation is needed for these checks. The local argument command supports Node 22.13 or newer and uses built-in TypeScript stripping; no npm package installation is required. Its vendored modules are unchanged production code, identified by a separate byte-level provenance record.

The GP15 quick check, source fetcher and research-trace verifier use the Python standard library. The fetcher restores two files from their original providers into ignored payload paths, verifies the frozen SHA-256 values and refuses mismatched bytes. It reuses verified existing files. Network access is needed only when either input is absent; no scientific source binding is rewritten.

The full ten-stage GP15 computation uses [`artifact/gp15/reproduction/requirements-research.lock`](../artifact/gp15/reproduction/requirements-research.lock), including NumPy 2.5.4 and Matplotlib 3.10.7 with pinned transitive dependencies. Package-index access is required for installation. Computation is local after installation and source recovery; it does not invoke DeepShovel, model services or model fitting.

The preserved **reference-environment execution** used Python 3.12.13 and passed ten stages with 76 generated files byte-identical. The unchanged `run_reproduction.py` is the strict entry point. Frozen programs record the full `sys.version` string in some outputs, and image bytes can depend on the platform. A matching Python patch number alone does not guarantee all strict byte comparisons on another platform. Reference outputs, scientific constants and source hashes must not be changed to obtain a passing run.

The separate cross-platform entry point is `artifact/gp15/run_portable.py --out NEW_DIRECTORY`. It executes the unchanged ten stages, permitting and reporting differences only in four specified Python-runtime fields and their verified dependent manifest entries. Scientific fields, CSVs, SVGs and PDFs retain strict byte comparison. Six explicitly named PNGs are checked as decoded pixels: the primary image must be pixel-identical; five final images must have byte-identical SVG/PDF counterparts, identical dimensions/mode/metadata, maximum channel difference 2/255 and changed-pixel ratio at most 0.0001. Only verified references to those PNG hashes and lengths may then differ. Every difference is reported; no scientific tolerance is introduced. The generated `portable-verification.json` identifies each comparison and permitted difference. Nothing is rewritten in the reference or generated files. Keep this report distinct from the strict reference result. Actual execution results are documented in the [GP15 package](../artifact/gp15/README.md). Configuring the Ubuntu job does not establish that the job has already passed; use its actual Actions logs for that revision.

The [argument package](../artifact/arguments/README.md), [GP15 package](../artifact/gp15/README.md), [historical research trace](../artifact/research-trace/README.md) and [English live walkthrough](../artifact/live-demo/README.md) are the main v1.1.0 entry points. Offline checks use recorded inputs and no production credentials. Their counts describe executed checks, rather than independent scientific observations or expert labels.

## Preserved earlier checks and output handling

Installation needs Python/npm package-index access for the earlier extended tier and full application. After installation, the production contract fixtures use only the bundled modules and fixture inputs. The JavaScript consumer installation is isolated in the run directory. The core tier has no third-party install. The earlier core, Linux/macOS extended and Web-build CI jobs remain.

Generate new results under new `runs/` directories; full-reproduction output paths must not already exist. Preserve historical receipts and scientific exports. CI uploads reports and stage logs, without republishing the disposable working copy or the two downloaded supporting inputs. Publication file identities are recorded in `repro/files.json` and package-specific manifests; generated run reports and self-referential manifests are excluded from their own identity lists.
