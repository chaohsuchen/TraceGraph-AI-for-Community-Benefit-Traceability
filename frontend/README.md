# TraceGraph frontend

Use Node.js 20 or newer.

    npm ci
    node frontend/dev.mjs

Open [the synthetic demo](http://127.0.0.1:4173/?demo=1). It starts with a renewable electricity commitment for the fictional Harbor Works. The graph includes quarterly progress and an unresolved disagreement between two reports. Select a finding to inspect its source and passage. Expand Harbor Works to see its other commitments, or search for “Harbor Works local jobs” to try clarification.

Use **Research again** to simulate rechecking the same findings and updating the assessment. The evidence and links keep their IDs. [The failed refresh demo](http://127.0.0.1:4173/?demo=1&fail-refresh=1) keeps the prior assessment and graph when the simulated provider fails.

Set TRACEGRAPH_BACKEND to the Data/API server origin, then open [the live API view](http://127.0.0.1:4173/). The development server forwards API requests to that origin.

Run the HTTP client and demo tests.

    node --test frontend/api.test.mjs