# Third party materials

- `artifact/vendor/pdfjs/` contains locally vendored PDF.js and related runtime resources under the included upstream license and notices. Its resource manifest records the distributed bytes.
- `artifact/second-case/source.pdf`: Taghipour et al. (2019), DOI 10.1007/s12182-019-0331-9, CC BY 4.0. See `artifact/second-case/SOURCE.md`.
- `artifact/nd-case/source/article.pdf`: Nestmeyer et al. (2026), DOI 10.33063/agc.v2i1.1099, CC BY 4.0. See `artifact/nd-case/SOURCE.md`. The author workbook is cited, not redistributed.
- `artifact/vendor/compiler/` is an earlier, byte-recorded excerpt of the same project, now released under this repository's AGPL-3.0-only license. Its source manifest identifies the earlier source bytes; the current full Compiler is in `compiler/`.
- Node and Python runtime dependencies are obtained from their registries and retain their own licenses. They are not relicensed by this repository.
- MinerU is accessed as an external parsing service and its package/model weights are not redistributed. See `compiler/THIRD_PARTY_NOTICES.md`.

Text-free graph projections contain only author-produced structural records and provenance metadata. No blanket license for the production scientific collection is granted.

## Scientific reasoning and GP15 materials in v1.1.0

- `artifact/arguments/vendor/` contains byte-recorded DeepShovel production modules under AGPL-3.0-only, with the original license and source provenance. The saved Asmari argument graph refers to Taghipour et al. (2019), attributed above. These author-created graph records do not replace the paper's attribution.
- The GP15 REE measurements are by **Alan M. Shiller and Melissa Gilbert**, BCO-DMO datasets [932161 version 1](https://doi.org/10.26008/1912/bco-dmo.932161.1) and [932559 version 1](https://doi.org/10.26008/1912/bco-dmo.932559.1). The pH measurements are by **Gregory Cutter and Nicole Buckley**, datasets [838157 version 2](https://doi.org/10.26008/1912/bco-dmo.838157.2) and [838173 version 2](https://doi.org/10.26008/1912/bco-dmo.838173.2). The unchanged data and accompanying method/intercalibration records retain their **CC BY 4.0** attribution. See the [GP15 source index](artifact/gp15/README.md#attribution-and-redistribution-scope).
- The original REE–pH model is by **Ping-Chun Lin and David C. Catling (2024)**, [DOI 10.2475/001c.118215](https://doi.org/10.2475/001c.118215). The article states **CC BY 4.0** on PDF page 17. Its unchanged PDF/text and identity are retained. Author software is pinned to commit `99ebf142d6787361c7720b7a3e7c213fd213eddb`; its [MIT copyright and permission notice](artifact/gp15/reproduction/payload/ree/round8/raw/Lin2024-LICENSE) remains unchanged. Neither the MIT notice nor the project license assigns a blanket license to upstream compiled measurements.
- The Hongo et al. (2007) research trace cites [DOI 10.2343/geochemj.41.1](https://doi.org/10.2343/geochemj.41.1), © 2007 The Geochemical Society of Japan. The publisher identifies free access; this artifact does not infer a redistribution license. The original PDF, figures and source-text quotations are excluded. The public trace retains author-created research notes, labeled model-derived claims and source identifiers/page locators. See its [publication and redaction scope](artifact/research-trace/README.md).
- The live walkthrough contains captures of the DeepShovel interface. Scientific content retains its identified source attribution; screenshots do not relicense underlying publications. Follow the [walkthrough](artifact/live-demo/README.md) for the recorded scope of each view.

Two supporting files are obtained directly from their original providers rather than committed. The fetcher reads the unchanged [source manifest](artifact/gp15/reproduction/payload/ree/round8/transfer-source-input.json), checks these SHA-256 values before writing and refuses changed provider bytes:

| Supporting input | Original provider | Frozen SHA-256 |
| --- | --- | --- |
| `REE_seawater_all_pmol.csv` | [Lin author repository, pinned commit](https://github.com/PingChunLin/REE_pH/blob/99ebf142d6787361c7720b7a3e7c213fd213eddb/REE_data/REE_seawater_all_pmol.csv) | `464b8764c855680aa4a622e9b511664943bd1e565653c4092a1fc992293b83e5` |
| GLODAPv2.2022 `Dataset_DOIs.csv` | [NOAA archive, release-specific catalogue](https://www.nodc.noaa.gov/archive/arc0206/0257247/1.1/data/0-data/Dataset_DOIs.csv) | `32357c3089a6a83b474bd0c4564a408088dadecb46386ef87b34d0cb0ee23ad8` |

Their restored local paths remain ignored by Git, and CI does not upload them as run artifacts. The full GLODAP merged product is not distributed. Consult and preserve the original providers' requirements when reusing compiled measurements. External runtime packages, including NumPy, Matplotlib and their dependencies, retain their own upstream licenses. No project-level license supersedes these notices.
