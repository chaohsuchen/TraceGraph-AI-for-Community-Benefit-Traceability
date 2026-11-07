# TraceGraph frontend

Run the dependency-free development server with Node.js 20 or newer.

    node frontend/dev.mjs

Open [the fixture demo](http://127.0.0.1:4173/?demo=1) to try search, clarification, organization expansion, evidence, refresh, and run history. [The failed refresh demo](http://127.0.0.1:4173/?demo=1&fail-refresh=1) preserves the previous graph after a simulated provider failure.

Set TRACEGRAPH_BACKEND to the Data/API server origin, then open [the live API view](http://127.0.0.1:4173/). The development server forwards API requests to that origin. The fixture demo remains available separately.

Run the HTTP client and fixture flow tests.

    node --test frontend/api.test.mjs
