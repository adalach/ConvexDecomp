# Search explorer

A local web app for tuning the convex decomposition. It runs the beam search on one footprint
with the score and search budget set by sliders, and draws the exact tree of states the search
made, as `tools/search_tree_figure.py` does.

```bash
python -m uvicorn app:app --app-dir tools/search_explorer --port 8765
```

Then open http://localhost:8765. The Python environment needs shapely, fastapi and uvicorn; the
app imports `convexdecomp` from this repository's `src/`.

| Part | What it does |
| --- | --- |
| Score sliders | `convexdecomp.core.scores.PartitionScore`: zones + w_C (1 − mean compactness) + w_R (1 − mean rectangularity) + narrow and small-zone penalties. The area weighting sets how much a zone's area counts in the means (1: by area, 0: every zone the same). Moving a slider re-ranks the found variants at once, then re-runs the search, because the score also decides which states the beam keeps. |
| Search sliders | Beam ranking (reflex vertices first, or score first), the mid-angle cut at every reflex vertex, the smallest corner a cut may create (0 = off), the thinnest zone allowed (a hard rule, 0 = off), the area weighting of the beam's score, beam width, depth, reflex vertices per piece, variants to find, dead ends before giving up, smallest piece a cut may leave, snap tolerance, adaptive budget. A setting the engine does not have yet is greyed out and not passed on. |
| Tree | Each state with the piece the search cuts next in colour, the rest in grey, and the cut that made it in orange. The ring tells what the search did with it. Click a state to see it large; use the small button to fold a branch, and "+N more" to show all children. |
| Variants | Every fully convex state the search found, plus reference decompositions, ranked by the current score with each term's share. |

| File | Contents |
| --- | --- |
| `app.py` | FastAPI server: `/api/examples`, `/api/engine` (which optional settings the engine has), `/api/run` (traced search, states, pieces with metrics). |
| `static/` | The page: `index.html`, `app.js` (d3 7.8.5 from cdnjs), `style.css`. |
| `paper_example.wkt` | The polygon of the EG-ICE 2026 search-tree figure, offered when the BEM4AI examples are missing. |

The examples come from `data/bem4ai/explorer_examples.json`: BEM4AI footprints with reference
decompositions (old engine, hand-made mixes, best of all hole openings). That file is local
and ignored by git; without it the app offers only `paper_example.wkt`.
