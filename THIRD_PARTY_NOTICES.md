# Third-party notices

Parts of this project are ported from the following MIT-licensed work. The
ported code was rewritten to fit this codebase. Each module that carries it
says so in its docstring.

## trader-skill and day-trader-skill

- https://github.com/Jakeschincariol/trader-skill
- https://github.com/Jakeschincariol/day-trader-skill

Used in:
- `backend/app/sources/congress.py`: the House Clerk index and PTR parsing (`trader-pelosi/ptr.py`).
- `backend/app/guardrails.py`: the per-trade limits (`trader-risk`).
- `backend/app/sources/holdings13f.py` and `backend/app/data/filers13f.py`: 13F reading, the quarter
  diff, issuer-name ticker matching, the filer list and the share-class table (`trader-copycat`).
- `backend/app/timemachine.py`: the Time Machine's replay, stop-first rule and verdict
  (`trader-timemachine`), adapted from 5-minute bars to a horizon of daily bars.
- `backend/app/taxlots.py`: lots, lot methods, terms, wash sales and harvesting (`trader-tax`).
- `backend/app/rebalance.py`: the drift band and sells-first plan (`trader-rebalance`).
- Later ports name their source in the same way.

The test fixtures under `backend/tests/fixtures/congress/` and
`backend/tests/fixtures/13f/` are excerpts of public House Clerk and SEC
EDGAR filings, taken from trader-skill's test suite.

```
MIT License

Copyright (c) 2026 Jake Schincariol

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
