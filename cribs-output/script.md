# script.md — ChartBreaker explainer

Format: `beat | narration | on-screen cue | claims`

## Chapter 1: What it is

1.1 | "This is ChartBreaker, a multi-agent adversarial evaluation platform for the Open E M R Clinical Co-Pilot." | name + tagline | C1
1.2 | "It keeps attacking a deployed clinical chatbot, probing for prompt injection, patient data leaks, tool misuse, and more." | six attack-category chips | C2
1.3 | "It's built for the engineers who run the red team, and for the people who sign off on the fixes." | operator and stakeholder personas | C3
1.4 | "When the Judge flags an exploit, it gets pinned as a regression test that runs on every deploy." | README quote, line 5 | C4
1.5 | "The Judge never sees the red team's reasoning. It checks each reply with deterministic verifiers, then, optionally, with an L L M." | README quote, line 88 | C5
1.6 | "A local dashboard lists every open vulnerability, with both verdicts side by side." | dashboard screenshot | C6

## Chapter 2: Code map

2.1 | "Here's how the code fits together." | empty canvas, title "Code map" | —
2.2 | "It starts with the Orchestrator. Each tick, it scores what's left and picks the next attack brief." | cluster "Orchestrator Scheduling" | C7, C8, C21
2.3 | "A campaign brief says what to attack. A specialist turns it into an attack attempt." | cluster "Attack Briefs & Envelopes" | C9, C10, C21
2.4 | "The target client sends each attempt to the live Co-Pilot and captures its response." | cluster "Target Client" | C11, C12, C21
2.5 | "The Judge scores that response. First deterministically, then, when it's switched on, with a semantic L L M verdict." | cluster "Judge" | C13, C14, C21
2.6 | "The observability store is the canonical record of each run: an S Q Lite database, plus a JSON lines log." | cluster "Observability Store" | C15, C16, C21
2.7 | "The C L I runs the loop, and the regression suite replays the pinned exploits against the live target." | cluster "CLI & Regression Suite" | C17, C18, C21
2.8 | "The most connected node in the whole graph is Observability Store. Every campaign, attempt, response, and verdict goes through it." | all clusters, ObservabilityStore highlighted with its edges | C19, C20
2.9 | "That's the tour of ChartBreaker." | end card | C1
