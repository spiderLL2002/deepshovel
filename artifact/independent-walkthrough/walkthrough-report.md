> Public copy: personal machine path prefixes have been normalized; scientific input/export files are unchanged.

# Single AI operator functional walkthrough

Date: 2026-10-27. One AI operator performed source reading and browser interaction in an isolated localhost origin. No paper manuscript, prepared task fields, previous exports, worksheets, source audits or other agent conclusions were read. Only server.py was inspected to establish isolation, plus the supplied retrieval JSON, complete graph through the UI, and original source PDF. Original 127.0.0.1 browser storage was not read or reset.

## Actual execution

The first clock observation was 12:35:55 UTC (20:35:55 Beijing). Source PDF was then read before importing retrieval and graph inputs. The saved decision timestamp is 12:38:18.516 UTC: 143.516 seconds after that clock observation. Source identity check occurred at 12:38:10.441 UTC. Export had been retrieved by 12:39:13 UTC (198 seconds after first clock observation); verification/report authoring followed. These are raw AI/tool wall-clock observations, include tools and recovery, do not cover earlier instructions/setup. Exact intermediate file modification timestamps are in verification.json; no finer per-action timings were reconstructed.

1. Inspected server.py: fixed port 8137, no port argument. Parent supplied isolated origin http://localhost:8137/generic-reuse.html, separate from its http://127.0.0.1:8137. Created new IAB tab. Initial page showed no imported result and no saved record. Did not Reset.
2. Read original source.pdf with pypdf after discovering default Python and bundled Python lacked PyMuPDF. Rendered complete PDF pages 10 and 11 with pdftoppm and visually inspected both. Read source text on pages 3, 4, 8–13 relevant to assumptions, models and F2 interpretation.
3. Task B first: imported retrieved-result-only.json through Retrieval JSON file chooser. UI said “Path lacks observations or source anchors; saving is unavailable.” Returned shortest path was sole path; observation/anchor menus had no real options; Save and Download were disabled. Did not fabricate nodes, fill substitute observations, or force saving. B-shortest-only.jpg and B-shortest-only-dom.txt preserve this failure. No decision export is possible for B.
4. Continued as Task A: imported full-graph.json through Stored graph JSON file chooser and clicked Build observation branch. UI reported 33 nodes and 33 edges, preserved shortest path, with scientific fields still blank. Chose Recorded full-graph observation branch, obs.f2.assessed, and anchor.text.f2.stability. UI showed linked graph status and the original quote and selector.
5. Independently typed source-derived scientific fields. Selected Interpretation and Reuse with qualification. Chose source.pdf in Local source PDF chooser. UI reported Verified file identity with SHA-256 f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99.
6. Clicked Save reuse decision. UI showed a saved premise, qualified reading conclusion, linked graph and verified file identity. Captured A-saved.jpg and A-saved-dom.txt.
7. First combined save/download attempt timed out after 30 seconds; save and screenshots had succeeded. Rebound the same tab, saw two Download saved JSON buttons (toolbar and saved summary), then targeted the button in Saved premise summary. Browser download succeeded at <author-workspace>/Downloads/observation-reuse-d5ec6f9d439e.json. Copied this actual downloaded file to A-final-export.json. No export was manufactured from application internals.
8. Verified exported canonical-payload SHA-256 against record ID and checked every original input-result field equals its export counterpart. All passed; original_result adds observation_evidence_path. Original shortest path and composite scope are preserved.

## Independently prepared reading

Quantity: estimated maximum sustainable total pore pressure, value 55–57 MPa, for F2 under the paper’s analytical gas-injection/fault-reactivation models. This is not the pressure increase. The paper’s prose summary is approximate and model dependent, not a statistical confidence interval; no statistical uncertainty level is stated.

Source locator: PDF page 11 of 14, section 6, right-column continuation paragraph immediately above section 7, final F2 sentence. Cross-check PDF p10 Table 3 F2 rows: Eq.3/Eq.5 Ppmax = 57.4/57.3 MPa, initial Ppi = 22.04 MPa, increments = 35.4/35.3 MPa. PDF p11 Table 4 F2 rows: Pc = 55.03/55.31 MPa; PCr = 57.58/57.81 MPa. Thus the prose 55–57 is not an exact envelope containing all tabulated outputs.

Conditions: normal faulting regime; cohesion zero and friction coefficient 0.6 assumed; F2 dip 80°, dip direction 8°, strike 278° (p4 Table 1); stresses taken within specified intervals (example 10 m) near the middle of fault planes (p11). Elastic-modulus alternatives and 3D Mohr/slip-tendency/modified-slip-tendency methods must remain attached. No general operational injection limit or new physical-safety validation follows.

## Obstacles, corrections and unresolved limits

- Significant source-location issue: recorded anchor page 11 and quote agree, but its normalized rectangle x=.07899, y=.79873, width=.41008, height=.11139 covers the left lower column. The F2 sentence is in the right column. The operator entered a corrected verbal locator and explicitly retained the discrepancy in notes. Original anchor was not changed. Hash and graph linkage do not certify rectangle accuracy.
- Source prose and table numeric precision differ. Preserved prose summary and stated table values rather than silently replacing it or treating spread as uncertainty.
- Missing source packages caused two read attempts to fail; bundled pypdf plus system pdftoppm worked. This is environment/tool overhead, not an app failure.
- IAB visible:true is unsupported in a subagent; creating background IAB tab succeeded.
- getByLabel for Recorded observation returned no match; refreshed UI and used observed combobox role successfully. This is locator adaptation, not proof human selection fails.
- Two visible Download saved JSON buttons made an unscoped download attempt ambiguous; scoped saved-summary control worked. The original timeout is preserved as a recovery step.
- App does not itself render the source during this workflow; source inspection was external via PDF extraction/rendering, then selected-file hash check in app. No claim is made that the app visually validated source passages.
- Task B’s structural deficiency remains unresolved without a complete graph; this is deliberately retained as failure.

## Outputs and interpretation boundary

A-final-export.json is the actual browser export. operator-fields.json records entered text; the export is authoritative. A-saved.jpg and B-shortest-only.jpg are actual full-page browser captures. source-10.png and source-11.png are whole-page PDF renders. verification.json includes preservation/hash checks and raw filesystem timestamps. This is one source-first AI functional walkthrough, with a successful qualified record and a blocked shortest-only input. It establishes no usability rate, time saving, human agreement or model correctness.
